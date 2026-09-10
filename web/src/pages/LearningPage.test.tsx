import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { LearningPage } from './LearningPage';
import { ApiError } from '../api/client';

const auth = vi.hoisted(() => ({ status: 'authenticated', username: 'alice', csrfToken: 'token', request: vi.fn() }));
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => auth }));

function view() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={client}><MemoryRouter><LearningPage /></MemoryRouter></QueryClientProvider>;
}

beforeEach(() => {
  auth.username = 'alice'; auth.request.mockReset();
  auth.request.mockImplementation(async (url: string) => url === '/api/v1/documents'
    ? { items: [{ document_id: 'doc', name: '资料', status: 'ready' }] }
    : { items: [], next_cursor: null });
});

it('shows real empty state and no unimplemented feature actions', async () => {
  render(view());
  expect(screen.getByRole('heading', { name: '学习中心' })).toBeVisible();
  expect(await screen.findByText('当前没有任务。')).toBeVisible();
  expect(screen.queryByRole('button', { name: '生成卡片' })).not.toBeInTheDocument();
});

it('creates an explicitly selected document plan', async () => {
  auth.request.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === '/api/v1/documents') return { items: [{ document_id:'doc', name:'资料', status:'ready' }] };
    if (options?.method === 'POST') return { plan:{ id:'plan', title:'我的计划' }, replayed:false };
    return { items:[], next_cursor:null };
  });
  render(view());
  fireEvent.click(screen.getByRole('button', { name:'学习计划' }));
  await screen.findByRole('option', { name:'资料' });
  fireEvent.change(screen.getByLabelText('学习文档'), { target:{ value:'doc' } });
  fireEvent.change(screen.getByLabelText('计划名称'), { target:{ value:'我的计划' } });
  fireEvent.click(screen.getByRole('button', { name:'创建计划' }));
  await waitFor(() => expect(auth.request.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(true));
  const [, options] = auth.request.mock.calls.find(([, options]) => options?.method === 'POST')!;
  expect(JSON.parse(options.body)).toMatchObject({ document_id:'doc', title:'我的计划', days:7, daily_minutes:30 });
  expect(await screen.findByText('计划已创建。')).toBeVisible();
});

it('clears draft when the authenticated user changes', async () => {
  const element = view();
  const rendered = render(element);
  fireEvent.click(screen.getByRole('button',{name:'学习计划'}));
  fireEvent.change(screen.getByLabelText('计划名称'), { target:{value:'私有草稿'} });
  auth.username = 'bob';
  rendered.rerender(view());
  fireEvent.click(screen.getByRole('button',{name:'学习计划'}));
  expect(screen.getByLabelText('计划名称')).toHaveValue('');
});

it('retains draft and request id after a failed create', async () => {
  let attempts = 0;
  auth.request.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === '/api/v1/documents') return { items:[{document_id:'doc',name:'资料',status:'ready'}] };
    if (options?.method === 'POST') {
      attempts++;
      if (attempts === 1) throw new Error('offline');
      return { plan:{id:'p',title:'保留草稿'}, replayed:true };
    }
    return {items:[],next_cursor:null};
  });
  render(view());
  fireEvent.click(screen.getByRole('button',{name:'学习计划'}));
  await screen.findByRole('option',{name:'资料'});
  fireEvent.change(screen.getByLabelText('学习文档'),{target:{value:'doc'}});
  fireEvent.change(screen.getByLabelText('计划名称'),{target:{value:'保留草稿'}});
  fireEvent.click(screen.getByRole('button',{name:'创建计划'}));
  await screen.findByRole('alert');
  expect(screen.getByLabelText('计划名称')).toHaveValue('保留草稿');
  fireEvent.click(screen.getByRole('button',{name:'创建计划'}));
  await screen.findByText('计划已创建。');
  const posts = auth.request.mock.calls.filter(([, options]) => options?.method === 'POST');
  expect(posts).toHaveLength(2);
  expect(JSON.parse(posts[0][1].body).request_id).toBe(JSON.parse(posts[1][1].body).request_id);
});

it('ignores late create results after switching users', async () => {
  let complete!: (value: unknown) => void;
  auth.request.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === '/api/v1/documents') return { items:[{document_id:'doc',name:'资料',status:'ready'}] };
    if (options?.method === 'POST') return new Promise(resolve => { complete=resolve; });
    return {items:[],next_cursor:null};
  });
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  const tree = () => <QueryClientProvider client={client}><MemoryRouter><LearningPage /></MemoryRouter></QueryClientProvider>;
  const rendered = render(tree());
  fireEvent.click(screen.getByRole('button',{name:'学习计划'}));
  await screen.findByRole('option',{name:'资料'});
  fireEvent.change(screen.getByLabelText('学习文档'),{target:{value:'doc'}});
  fireEvent.change(screen.getByLabelText('计划名称'),{target:{value:'私有计划'}});
  fireEvent.click(screen.getByRole('button',{name:'创建计划'}));
  auth.username='bob';
  rendered.rerender(tree());
  complete({plan:{id:'private',title:'私有计划'},replayed:false});
  await screen.findByText('当前没有任务。');
  fireEvent.click(screen.getByRole('button',{name:'学习计划'}));
  expect(screen.queryByText('计划已创建。')).not.toBeInTheDocument();
  expect(screen.queryByText('私有计划 · 任务')).not.toBeInTheDocument();
});

it('reports a task version conflict without automatic overwrite', async () => {
  auth.request.mockImplementation(async (url: string, options?: RequestInit) => {
    if (options?.method === 'PATCH') throw new ApiError(409,'LEARNING_VERSION_CONFLICT','任务已更新，请刷新后重试');
    if (url === '/api/v1/documents') return {items:[]};
    return {items:[{id:'t',plan_id:'p',document_id:'d',title:'阅读理解',due_date:'2026-09-07',duration_minutes:30,completed:false,completed_at:null,version:1}],next_cursor:null};
  });
  render(view());
  fireEvent.click(await screen.findByRole('button',{name:'标记完成'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('任务已更新');
  expect(auth.request.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1);
  expect(screen.getByRole('button',{name:'刷新任务'})).toBeVisible();
});

it('can continue an empty filtered page that still has a cursor', async () => {
  auth.request.mockImplementation(async (url: string) => {
    if (url === '/api/v1/documents') return {items:[]};
    return {items:[],next_cursor:url.includes('cursor=') ? null : 'next-page'};
  });
  render(view());
  await screen.findByText('当前没有任务。');
  fireEvent.click(screen.getByRole('button',{name:'下一页'}));
  await waitFor(() => expect(auth.request.mock.calls.some(([url]) => url.includes('cursor=next-page'))).toBe(true));
  expect(await screen.findByRole('button',{name:'回到第一页'})).toBeEnabled();
});

it('completes and restores a task using returned versions', async () => {
  let task = {id:'t',plan_id:'p',document_id:'doc',title:'阅读理解',due_date:'2026-09-07',duration_minutes:30,completed:false,completed_at:null as string | null,version:1};
  auth.request.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === '/api/v1/documents') return {items:[]};
    if (options?.method === 'PATCH') {
      const input = JSON.parse(options.body as string);
      task={...task,completed:input.completed,version:task.version+1};
      return {task,replayed:false};
    }
    return {items:[task],next_cursor:null};
  });
  render(view());
  fireEvent.click(await screen.findByRole('button',{name:'标记完成'}));
  const restore = await screen.findByRole('button',{name:'恢复未完成'});
  await waitFor(() => expect(restore).toBeEnabled());
  fireEvent.click(restore);
  await screen.findByRole('button',{name:'标记完成'});
  const updates=auth.request.mock.calls.filter(([,options]) => options?.method==='PATCH').map(([,options]) => JSON.parse(options.body));
  expect(updates.map(input => [input.expected_version,input.completed])).toEqual([[1,true],[2,false]]);
});
