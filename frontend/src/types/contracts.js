/** @typedef {{id:number, title:string, payload:object, author:string}} BusinessCase */
/** @typedef {{id:number, case_id:number, status:'queued'|'running'|'complete'|'failed',
 *   delegation_version_id:number|null, previous_run_id:number|null}} Run */
/** @typedef {{id:number, version_no:number, digest:string, source:string,
 *   source_ref:string|null, published_at:string|null}} DelegationVersion */
/** @typedef {{id:number, kind:'urgent_revoke'|'handover', handover_ref:string|null,
 *   revoke:Array<object>, reassign:Array<object>, status:'pending'|'published',
 *   version:DelegationVersion|null}} DispatchCorrection */
/** @typedef {{id:number, run_id:number, digest:string,
 *   delegation_version_no:number|null,
 *   report:{summary:string, metrics:object, sections:Array,
 *   delegation_version?:{version_no:number, digest:string, source:string,
 *   source_ref:string|null, published_at:string|null}}}} Evidence */
