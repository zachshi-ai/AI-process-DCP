import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Card } from './ui/Card';
import { Input } from './ui/Input';
import { Button } from './ui/Button';
import { MbJsonTablePage } from './MbJsonTablePage';

type HistoryEvent = {
  id: number;
  ts?: number;
  kind?: string;
  title?: string;
  url?: string;
  status?: string;
};

/**
 * 人机评测（顶层页面）：允许用户批量勾选历史记录，将其合并导入到测试表结构中做编辑/导出。
 */
export function MbBatchAnalysisPage(props: { apiBase: string }) {
  const apiBase = props.apiBase;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [events, setEvents] = useState<HistoryEvent[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [importedIds, setImportedIds] = useState<number[]>([]);
  const [expanded, setExpanded] = useState(false);

  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  useEffect(() => {
    let cancelled = false;
    const fetchEvents = async () => {
      setLoading(true);
      setError('');
      try {
        const res = await axios.get(`${apiBase}/api/history/events`, {
          params: { q: query || undefined, limit: 80, offset: 0 },
        });
        if (cancelled) return;
        setEvents(res.data?.events || []);
      } catch (e: any) {
        if (cancelled) return;
        setError(e?.response?.data?.detail || e?.message || '加载任务记录失败');
        setEvents([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchEvents();
    return () => {
      cancelled = true;
    };
  }, [apiBase, query]);

  const toggleSelected = (id: number) => {
    setSelectedIds((prev) => {
      const s = new Set(prev || []);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return Array.from(s.values());
    });
  };

  const handleSelectAll = () => {
    const ids = (events || []).map((e) => Number(e.id)).filter((x) => Number.isFinite(x) && x > 0);
    setSelectedIds(ids);
  };

  const handleClearSelection = () => {
    setSelectedIds([]);
  };

  const handleImport = () => {
    const ids = selectedIds.slice().sort((a, b) => a - b);
    setImportedIds(ids);
  };

  const handleClearImported = () => {
    setImportedIds([]);
    setExpanded(false);
  };

  return (
    <>
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 transform transition-all duration-500 animate-in fade-in slide-in-from-bottom-4">
        <Card className="lg:col-span-5 h-fit sticky top-28">
          <h2 className="text-2xl font-serif font-bold mb-6">人机评测</h2>

          <div className="space-y-4">
            <Input label="搜索任务记录" type="text" value={query} onChange={(e: any) => setQuery(e.target.value)} placeholder="按标题或 URL 搜索" />

            <div className="flex gap-2 flex-wrap">
              <Button type="button" variant="secondary" onClick={handleSelectAll} disabled={loading || events.length === 0}>
                全选
              </Button>
              <Button type="button" variant="ghost" onClick={handleClearSelection} disabled={loading || selectedIds.length === 0}>
                清空选择
              </Button>
            </div>

            <div className="flex gap-2 flex-wrap">
              <Button type="button" variant="cta" onClick={handleImport} disabled={selectedIds.length === 0}>
                导入选中（{selectedIds.length}）
              </Button>
              <Button type="button" variant="ghost" onClick={handleClearImported} disabled={importedIds.length === 0}>
                清空已导入
              </Button>
            </div>

            {error ? <div className="text-sm text-red-600">{error}</div> : null}
            {loading ? <div className="text-sm text-gray-500">正在加载任务记录...</div> : null}

            {!loading && events.length === 0 ? <div className="text-sm text-gray-500">暂无任务记录</div> : null}

            <div className="space-y-2 max-h-[520px] overflow-auto pr-1">
              {(events || []).map((ev) => {
                const checked = selectedIdSet.has(ev.id);
                const ts = ev.ts ? new Date(ev.ts * 1000).toLocaleString() : '';
                return (
                  <label key={ev.id} className="flex items-start gap-3 p-3 rounded-xl border border-gray-100 bg-white hover:bg-gray-50 cursor-pointer">
                    <input type="checkbox" checked={checked} onChange={() => toggleSelected(ev.id)} className="mt-1 h-4 w-4" />
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-brand-text truncate">
                        #{ev.id} {String(ev.title || '').trim() || '未命名'}
                      </div>
                      <div className="mt-1 text-xs text-gray-500 break-all">{ev.url}</div>
                      {ts ? <div className="mt-1 text-[11px] text-gray-400">创建时间 {ts}</div> : null}
                    </div>
                  </label>
                );
              })}
            </div>
          </div>
        </Card>

        <Card className="lg:col-span-7 min-h-[600px] flex flex-col bg-white/50">
          <div className="flex items-center justify-between gap-3 mb-6">
            <h2 className="text-2xl font-serif font-bold">测试表</h2>
            {importedIds.length > 0 ? (
              <Button type="button" variant="secondary" onClick={() => setExpanded(true)}>
                大视图展开
              </Button>
            ) : null}
          </div>
          {importedIds.length === 0 ? (
            <div className="flex-1 flex items-center justify-center text-gray-400/80">
              <div className="text-sm">左侧勾选历史记录后点击“导入选中”生成测试表</div>
            </div>
          ) : (
            <MbJsonTablePage apiBase={apiBase} eventIds={importedIds} />
          )}
        </Card>
      </div>

      {expanded && importedIds.length > 0 ? (
        <div className="fixed inset-0 z-50 bg-black/40 p-3 sm:p-6" onClick={() => setExpanded(false)}>
          <div
            className="w-full h-full bg-white rounded-2xl shadow-soft border border-white/40 backdrop-blur-sm p-4 sm:p-6 flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-3 mb-4">
              <div className="text-xl font-serif font-bold">测试表（大视图）</div>
              <Button type="button" variant="secondary" onClick={() => setExpanded(false)}>
                关闭
              </Button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <div className="h-full overflow-auto">
                <MbJsonTablePage apiBase={apiBase} eventIds={importedIds} wide />
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
