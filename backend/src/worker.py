import logging
from pathlib import Path
from threading import Event, Thread
from time import sleep

from src.config.settings import ARTIFACTS, cache, engine
from src.services import corrections
from src.services.domain import solve
from src.services.workflow import claim, finish
from src.models.tables import schema

HEARTBEAT = Path(ARTIFACTS) / 'worker-heartbeat'


def heartbeat(stop):
    while not stop.is_set():
        HEARTBEAT.touch()
        stop.wait(2)


def work():
    HEARTBEAT.unlink(missing_ok=True)
    while True:
        try:
            schema.create_all(engine)
            with engine.begin() as conn:
                corrections.ensure_baseline(conn)
            break
        except Exception:
            logging.exception('Worker bootstrap failed')
            sleep(2)
    while True:
        try:
            job = claim()
            HEARTBEAT.touch()
            if not job:
                cache.brpop('worker:wake', timeout=1)
                continue
            run_id, token, payload, version = job
            stop = Event()
            thread = Thread(target=heartbeat, args=(stop,), daemon=True)
            thread.start()
            try:
                result = solve(payload, engine, snapshot=version['snapshot'] if version else None)
                finish(run_id, token, result=result, version=version)
            except (ValueError, KeyError, TypeError) as exc:
                finish(run_id, token, error=str(exc)[:500], version=version)
            except Exception:
                logging.exception('Domain run failed: %s', run_id)
                finish(run_id, token, error='后台执行失败，请检查输入或查看 worker 日志。', version=version)
            finally:
                stop.set()
                thread.join()
        except Exception:
            logging.exception('Worker loop failure')
            sleep(2)


if __name__ == '__main__':
    work()
