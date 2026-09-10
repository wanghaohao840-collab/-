import type { AuthRequest } from '../documents/api';
import type { CreatePlan, LearningPlan, LearningTask, UpdateTask } from './types';

export function createPlan(request: AuthRequest, input: CreatePlan) {
  return request<{ plan: LearningPlan; replayed: boolean }>('/api/v1/learning/plans', {
    method:'POST', cache:'no-store', headers:{ 'Content-Type':'application/json' }, body:JSON.stringify(input),
  });
}
export function updateTask(request: AuthRequest, id: string, input: UpdateTask) {
  return request<{ task: LearningTask; replayed: boolean }>(`/api/v1/learning/tasks/${encodeURIComponent(id)}`, {
    method:'PATCH', cache:'no-store', headers:{ 'Content-Type':'application/json' }, body:JSON.stringify(input),
  });
}
