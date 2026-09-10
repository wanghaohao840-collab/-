export type Page<T> = { items: T[]; next_cursor: string | null };
export type LearningPlan = {
  id: string; document_id: string; document_name: string; title: string; timezone: string;
  start_date: string; target_date: string; daily_minutes: number; status: 'active' | 'completed';
  version: number; created_at: string; updated_at: string; task_count: number; completed_count: number;
};
export type LearningTask = {
  id: string; plan_id: string; document_id: string; due_date: string; phase: string; title: string;
  duration_minutes: number; completed: boolean; completed_at: string | null; version: number;
};
export type CreatePlan = { request_id: string; document_id: string; title: string; days: number; daily_minutes: number; timezone: string };
export type UpdateTask = { request_id: string; expected_version: number; completed: boolean };
