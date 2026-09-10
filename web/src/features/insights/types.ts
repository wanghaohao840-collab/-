export type ActivityDay = { date: string; documents: number; questions: number; notes: number; total: number };
export type LearningStats = {
  document_count: number; completed_question_count: number; note_count: number;
  report_count: number; active_days: number; window_days: number; activity: ActivityDay[];
};
export type Overview = {
  stats: LearningStats;
  recent_documents: Array<{ document_id: string; name: string; loaded_at: string | null }>;
  recent_questions: Array<{ question: string; asked_at: string; document_names: string[] }>;
};
export type LearningReportItem = { id: string; title: string; created_at: string };
export type LearningReport = LearningReportItem & { content: string };
