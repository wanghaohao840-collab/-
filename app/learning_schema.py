"""Additive learning-plan tables; legacy JSON migration is explicit, not startup work."""

LEARNING_SCHEMA = """
create table if not exists learning_plans (
 user_id text not null references users(id) on delete cascade,
 id text not null, title text not null check(length(trim(title)) between 1 and 100),
 timezone text not null, start_date text not null, target_date text not null,
 daily_minutes integer not null check(daily_minutes between 5 and 480),
 status text not null check(status in ('active','completed')),
 version integer not null default 1 check(version >= 1),
 created_at text not null, updated_at text not null,
 primary key(user_id,id), check(start_date <= target_date)
);
create table if not exists learning_plan_documents (
 user_id text not null references users(id) on delete cascade,
 plan_id text not null, document_id text not null, document_name text not null,
 primary key(user_id,plan_id), unique(user_id,plan_id,document_id),
 foreign key(user_id,plan_id) references learning_plans(user_id,id) on delete cascade
);
create table if not exists learning_tasks (
 user_id text not null references users(id) on delete cascade,
 id text not null, plan_id text not null, document_id text not null,
 due_date text not null,
 phase text not null check(phase in ('reading','cards','exercises','review')),
 title text not null, duration_minutes integer not null check(duration_minutes between 5 and 480),
 completed integer not null default 0 check(completed in (0,1)), completed_at text,
 version integer not null default 1 check(version >= 1),
 primary key(user_id,id), unique(user_id,plan_id,id), unique(user_id,plan_id,due_date),
 foreign key(user_id,plan_id,document_id)
  references learning_plan_documents(user_id,plan_id,document_id) on delete cascade,
 check((completed=0 and completed_at is null) or (completed=1 and completed_at is not null))
);
create table if not exists learning_requests (
 user_id text not null references users(id) on delete cascade,
 request_id text not null, operation text not null check(operation in ('create_plan','set_task_state')),
 request_digest text not null, resource_id text not null,
 result_version integer not null check(result_version >= 1), created_at text not null,
 primary key(user_id,request_id)
);
create table if not exists learning_task_events (
 user_id text not null references users(id) on delete cascade,
 id text not null, plan_id text not null, task_id text not null, request_id text not null,
 from_completed integer not null check(from_completed in (0,1)),
 to_completed integer not null check(to_completed in (0,1)),
 occurred_at text not null, result_version integer not null check(result_version >= 2),
 primary key(user_id,id), unique(user_id,request_id),
 foreign key(user_id,plan_id,task_id) references learning_tasks(user_id,plan_id,id) on delete cascade,
 foreign key(user_id,request_id) references learning_requests(user_id,request_id),
 check(from_completed != to_completed)
);
create table if not exists learning_migrations (
 user_id text not null references users(id) on delete cascade,
 source_sha256 text not null, migration_version integer not null check(migration_version=1),
 source_schema_version integer not null check(source_schema_version=1),
 timezone text not null, imported_plan_count integer not null check(imported_plan_count>=0),
 imported_task_count integer not null check(imported_task_count>=0),
 normalized_sha256 text not null, completed_at text not null,
 primary key(user_id,migration_version)
);
create index if not exists ix_learning_plans_page on learning_plans(user_id,created_at desc,id desc);
create index if not exists ix_learning_documents on learning_plan_documents(user_id,document_id,plan_id);
create index if not exists ix_learning_tasks_page on learning_tasks(user_id,plan_id,due_date,id);
create index if not exists ix_learning_tasks_due on learning_tasks(user_id,completed,due_date,id);
create index if not exists ix_learning_tasks_completed on learning_tasks(user_id,completed,completed_at desc,id desc);
"""
