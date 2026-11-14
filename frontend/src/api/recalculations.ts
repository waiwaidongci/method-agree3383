import {request,post} from './client';import type {RecalculationBatch} from '../types/domain';
export const getBatches=()=>request<RecalculationBatch[]>('/recalculations');
export const getBatch=(id:number)=>request<RecalculationBatch>('/recalculations/'+id);
export const createBatch=(data:unknown)=>post<RecalculationBatch>('/recalculations',data);
export const revokeBatch=(id:number)=>post<RecalculationBatch>('/recalculations/'+id+'/revoke',{});
export const activateBatch=(id:number)=>post<RecalculationBatch>('/recalculations/'+id+'/activate',{});
