import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Card } from './components/ui/Card';
import { Input } from './components/ui/Input';
import { Button } from './components/ui/Button';
import { PrettyJsonViewer } from './components/PrettyJsonViewer';

const normalizeApiBase = (v: string) => (v || '').trim().replace(/\/+$/, '');

const isValidApiBase = (v: string) => {
  try {
    const u = new URL(normalizeApiBase(v));
    const port = Number(u.port);
    if (!u.protocol || (u.protocol !== 'http:' && u.protocol !== 'https:')) return false;
    if (!Number.isFinite(port) || port <= 0) return false;
    return true;
  } catch (_) {
    return false;
  }
};

export default function JsonViewerTestApp() {
  const [apiBase, setApiBase] = useState('http://127.0.0.1:8000');
  const base = useMemo(() => normalizeApiBase(apiBase), [apiBase]);

  const [events, setEvents] = useState<any[]>([]);
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null);
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const refreshEvents = async () => {
    if (!isValidApiBase(base)) {
      setError('后端地址不可用，请填写类似 http://127.0.0.1:8000');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const res = await axios.get(`${base}/api/history/events`, { params: { limit: 30, offset: 0 } });
      setEvents(res.data?.events || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '无法获取历史列表');
    } finally {
      setLoading(false);
    }
  };

  const selectEvent = async (id: number) => {
    setSelectedEventId(id);
    setLoading(true);
    setError('');
    try {
      const res = await axios.get(`${base}/api/history/events/${id}`);
      setItems(res.data?.items || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '无法获取历史详情');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshEvents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen bg-brand-bg text-brand-text font-sans antialiased">
      <div className="max-w-6xl mx-auto px-6 py-10">
        <Card className="mb-8">
          <div className="flex items-end gap-3 flex-wrap">
            <div className="flex-1 min-w-[260px]">
              <Input
                label="后端地址 (API_BASE)"
                type="url"
                value={apiBase}
                onChange={(e: any) => setApiBase(e.target.value)}
                helperText="示例: http://127.0.0.1:8000"
              />
            </div>
            <Button type="button" variant="primary" onClick={refreshEvents} isLoading={loading}>
              刷新历史
            </Button>
          </div>
          {error && <div className="mt-4 text-sm text-red-600 break-words">{error}</div>}
          <div className="mt-4 text-xs text-gray-500 break-all">当前 API_BASE: {base}</div>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          <Card className="lg:col-span-4 h-fit">
            <div className="text-lg font-bold font-serif mb-4">历史任务（测试页）</div>
            <div className="space-y-2 max-h-[70vh] overflow-auto pr-2">
              {events.length === 0 && <div className="text-sm text-gray-500">暂无历史</div>}
              {events.map((ev) => (
                <button
                  key={ev.id}
                  type="button"
                  onClick={() => selectEvent(ev.id)}
                  className={`w-full text-left p-3 rounded-xl border transition-all ${
                    selectedEventId === ev.id ? 'border-brand-primary bg-brand-primary/5' : 'border-gray-100 bg-white hover:border-brand-primary/40'
                  }`}
                >
                  <div className="text-sm font-semibold truncate">{ev.title || `#${ev.id}`}</div>
                  <div className="mt-1 text-xs text-gray-500 break-all">{ev.url}</div>
                  <div className="mt-2 text-xs text-gray-600">{ev.status || '-'}</div>
                </button>
              ))}
            </div>
          </Card>

          <Card className="lg:col-span-8 min-h-[70vh]">
            <div className="text-lg font-bold font-serif mb-4">明细 JSON 展示（测试版）</div>
            {loading && <div className="text-sm text-gray-500">加载中…</div>}
            {!loading && selectedEventId == null && <div className="text-sm text-gray-500">左侧选择一个历史任务查看明细</div>}
            {!loading && selectedEventId != null && (
              <div className="space-y-4">
                {items.length === 0 && <div className="text-sm text-gray-500">该任务暂无明细</div>}
                {items.map((it) => (
                  <div key={it.id} className="rounded-2xl border border-gray-100 bg-white/70 p-4">
                    <div className="text-sm font-semibold break-all">{it.url}</div>
                    <div className="mt-2 text-xs text-gray-600">状态: {it.status || '-'}</div>
                    {it.skill && <div className="mt-1 text-xs text-gray-600">技能: {it.skill}</div>}
                    {it.evidence && <div className="mt-1 text-xs text-gray-600 break-words">证据: {it.evidence}</div>}
                    <div className="mt-3">
                      <PrettyJsonViewer value={it.result || ''} defaultMode="fields" />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
