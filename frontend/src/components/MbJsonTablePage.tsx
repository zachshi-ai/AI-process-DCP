import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Button } from './ui/Button';
import { Input } from './ui/Input';

type MbColumn = {
  key: string;
  label: string;
  editable?: boolean;
  defaultVisible?: boolean;
};

type MbRow = {
  item_id: number;
  [key: string]: any;
};

type ExtraField = {
  source: 'input' | 'output';
  path: string;
  label?: string;
};

type LocalRow = MbRow & {
  __dirty?: boolean;
  __saving?: boolean;
  __saveError?: string;
};

/**
 * 将任意值标准化为 textarea/table 可显示的文本（统一换行、避免 null/undefined）。
 */
const normalizeText = (v: any) => {
  return String(v ?? '').replace(/\r\n/g, '\n');
};

/**
 * 判断值是否需要“折叠显示”，避免长文本把表格行撑得很高，让人误以为只导入了 1 行。
 */
const isLongText = (v: any) => {
  const s = String(v ?? '');
  if (!s) return false;
  if (s.length > 300) return true;
  if (s.includes('\n')) return true;
  return false;
};

/**
 * 表格分析表格组件：按 mb_JSON_test.xlsx 的结构展示数据，支持编辑并保存：
 * - 实际流程结果（默认通过）
 * - 问题点核心简述
 * - 调整措施
 */
export function MbJsonTablePage(props: { apiBase: string; eventIds: number[]; wide?: boolean; headerTitle?: string; onClose?: () => void }) {
  const apiBase = props.apiBase;
  const eventIds = (props.eventIds || []).filter((x) => Number(x) > 0);
  const wide = Boolean(props.wide);
  const headerTitle = props.headerTitle;
  const onClose = props.onClose;
  const eventKey = JSON.stringify(eventIds.slice().sort((a, b) => a - b));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [columns, setColumns] = useState<MbColumn[]>([]);
  const [rows, setRows] = useState<LocalRow[]>([]);
  const [exporting, setExporting] = useState(false);
  const [showPanel, setShowPanel] = useState(true);
  const [fieldsLoading, setFieldsLoading] = useState(false);
  const [availableFields, setAvailableFields] = useState<ExtraField[]>([]);
  const [fieldQuery, setFieldQuery] = useState('');
  const [selectedExtraFields, setSelectedExtraFields] = useState<ExtraField[]>([]);
  const [visibleKeys, setVisibleKeys] = useState<string[]>([]);

  const editableKeys = useMemo(() => {
    return new Set((columns || []).filter((c) => c.editable).map((c) => c.key));
  }, [columns]);

  const visibleKeySet = useMemo(() => new Set((visibleKeys || []).filter((k) => String(k || '').trim())), [visibleKeys]);

  const shownColumns = useMemo(() => {
    if (!visibleKeys.length) return columns || [];
    return (columns || []).filter((c) => visibleKeySet.has(c.key));
  }, [columns, visibleKeySet, visibleKeys.length]);

  const fieldOptions = useMemo(() => {
    const q = (fieldQuery || '').trim().toLowerCase();
    const selectedSet = new Set((selectedExtraFields || []).map((f) => `${f.source}.${f.path}`));
    return (availableFields || [])
      .filter((f) => !selectedSet.has(`${f.source}.${f.path}`))
      .filter((f) => {
        if (!q) return true;
        const label = `${f.source}.${f.path}`.toLowerCase();
        return label.includes(q);
      })
      .slice(0, 200);
  }, [availableFields, fieldQuery, selectedExtraFields]);

  useEffect(() => {
    if (!eventIds.length) return;
    let cancelled = false;
    const fetchTable = async () => {
      setLoading(true);
      setError('');
      try {
        const res = await axios.post(`${apiBase}/api/mb_table/collect`, {
          event_ids: eventIds,
          extra_fields: selectedExtraFields,
        });
        if (cancelled) return;
        const incomingCols: MbColumn[] = res.data?.columns || [];
        setColumns(incomingCols);
        const incoming: MbRow[] = res.data?.rows || [];
        setRows(incoming.map((r) => ({ ...r, __dirty: false, __saving: false, __saveError: '' })));

        setVisibleKeys((prev) => {
          if ((prev || []).length > 0) return prev;
          const initial = (incomingCols || [])
            .filter((c) => c.defaultVisible !== false)
            .map((c) => c.key)
            .filter((k) => String(k || '').trim());
          return initial;
        });
      } catch (e: any) {
        if (cancelled) return;
        setError(e?.response?.data?.detail || e?.message || '加载表格数据失败');
        setColumns([]);
        setRows([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchTable();
    return () => {
      cancelled = true;
    };
  }, [apiBase, eventKey, JSON.stringify(selectedExtraFields)]);

  useEffect(() => {
    if (!eventIds.length) return;
    let cancelled = false;
    const fetchFields = async () => {
      setFieldsLoading(true);
      try {
        const res = await axios.post(`${apiBase}/api/mb_table/fields`, { event_ids: eventIds });
        if (cancelled) return;
        setAvailableFields((res.data?.fields || []) as ExtraField[]);
      } catch (e: any) {
        if (cancelled) return;
        setAvailableFields([]);
      } finally {
        if (!cancelled) setFieldsLoading(false);
      }
    };
    fetchFields();
    return () => {
      cancelled = true;
    };
  }, [apiBase, eventKey]);

  const handleCellChange = (itemId: number, key: string, value: string) => {
    setRows((prev) =>
      (prev || []).map((r) => {
        if (r.item_id !== itemId) return r;
        return { ...r, [key]: value, __dirty: true, __saveError: '' };
      }),
    );
  };

  const handleSaveRow = async (itemId: number) => {
    const row = (rows || []).find((r) => r.item_id === itemId);
    if (!row) return;
    setRows((prev) => (prev || []).map((r) => (r.item_id === itemId ? { ...r, __saving: true, __saveError: '' } : r)));
    try {
      const payload = {
        problem_core: normalizeText(row.problem_core ?? ''),
        adjustment_measures: normalizeText(row.adjustment_measures ?? ''),
        actual_result: normalizeText(row.actual_result ?? ''),
      };
      await axios.put(`${apiBase}/api/history/items/${itemId}/mb_notes`, payload);
      setRows((prev) =>
        (prev || []).map((r) => (r.item_id === itemId ? { ...r, __dirty: false, __saving: false, __saveError: '' } : r)),
      );
    } catch (e: any) {
      setRows((prev) =>
        (prev || []).map((r) =>
          r.item_id === itemId
            ? { ...r, __saving: false, __saveError: e?.response?.data?.detail || e?.message || '保存失败' }
            : r,
        ),
      );
    }
  };

  /**
   * 导出 Excel：直接下载后端生成的 xlsx 文件。
   */
  const handleExportExcel = async () => {
    if (!eventIds.length) return;
    setExporting(true);
    try {
      const res = await axios.post(
        `${apiBase}/api/mb_table/export.xlsx`,
        {
          event_ids: eventIds,
          extra_fields: selectedExtraFields,
          visible_keys: visibleKeys.length ? visibleKeys : undefined,
        },
        { responseType: 'blob' },
      );
      const blob = new Blob([res.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = eventIds.length === 1 ? `mb_JSON_test_event_${eventIds[0]}.xlsx` : `mb_JSON_test_export.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '导出失败');
    } finally {
      setExporting(false);
    }
  };

  const toggleVisibleKey = (k: string) => {
    setVisibleKeys((prev) => {
      const s = new Set(prev || []);
      if (s.has(k)) s.delete(k);
      else s.add(k);
      return Array.from(s.values());
    });
  };

  const addExtraField = (f: ExtraField) => {
    setSelectedExtraFields((prev) => {
      const key = `${f.source}.${f.path}`;
      const exists = new Set((prev || []).map((x) => `${x.source}.${x.path}`)).has(key);
      if (exists) return prev || [];
      const next = [...(prev || []), f];
      return next;
    });
    setVisibleKeys((prev) => {
      const k = `${f.source}.${f.path}`;
      const s = new Set(prev || []);
      s.add(k);
      return Array.from(s.values());
    });
  };

  const removeExtraField = (f: ExtraField) => {
    const key = `${f.source}.${f.path}`;
    setSelectedExtraFields((prev) => (prev || []).filter((x) => `${x.source}.${x.path}` !== key));
    setVisibleKeys((prev) => (prev || []).filter((x) => x !== key));
  };

  return (
    <div>
      {error ? (
        <div className="bg-red-50 text-red-600 p-4 rounded-xl mb-4 border border-red-100">
          <div className="text-sm font-medium">{error}</div>
        </div>
      ) : null}

      {headerTitle ? (
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="text-xl font-semibold">{headerTitle}</div>
          <div className="border border-gray-200/70 bg-white/70 rounded-2xl p-1 flex items-center gap-2">
            <Button type="button" variant="secondary" onClick={() => setShowPanel((v) => !v)}>
              {showPanel ? '切换到表格视图' : '切换到字段配置视图'}
            </Button>
            <Button type="button" variant="primary" isLoading={exporting} onClick={handleExportExcel}>
              导出 Excel
            </Button>
            {onClose ? (
              <Button
                type="button"
                variant="ghost"
                onClick={onClose}
                className="border border-gray-200 bg-white hover:bg-gray-50"
              >
                关闭
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-end mb-3">
          <div className="border border-gray-200/70 bg-white/70 rounded-2xl p-1 flex items-center gap-2">
            <Button type="button" variant="secondary" onClick={() => setShowPanel((v) => !v)}>
              {showPanel ? '切换到表格视图' : '切换到字段配置视图'}
            </Button>
            <Button type="button" variant="primary" isLoading={exporting} onClick={handleExportExcel}>
              导出 Excel
            </Button>
          </div>
        </div>
      )}

      {showPanel ? (
        <div className="mb-4 border border-gray-100 rounded-xl bg-white p-4 space-y-4">
          <div>
            <div className="text-sm font-semibold text-gray-700 mb-2">显示列（勾选=显示，取消=隐藏）</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {(columns || []).map((c) => (
                <label key={c.key} className="flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" checked={visibleKeySet.has(c.key)} onChange={() => toggleVisibleKey(c.key)} />
                  <span className="truncate">{c.label}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="border-t border-gray-100 pt-4">
            <div className="text-sm font-semibold text-gray-700 mb-2">新增字段列（来自输入/输出 JSON，支持搜索、多选）</div>
            <Input
              label="搜索字段路径"
              type="text"
              value={fieldQuery}
              onChange={(e: any) => setFieldQuery(e.target.value)}
              placeholder="例如：formData.费用类别 或 风险等级"
            />
            {fieldsLoading ? <div className="text-xs text-gray-500 mt-2">正在加载可选字段...</div> : null}
            {!fieldsLoading && fieldOptions.length === 0 ? <div className="text-xs text-gray-500 mt-2">暂无可新增字段</div> : null}
            <div className="mt-2 max-h-[220px] overflow-auto border border-gray-100 rounded-lg">
              {(fieldOptions || []).map((f) => (
                <button
                  key={`${f.source}.${f.path}`}
                  type="button"
                  onClick={() => addExtraField(f)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-gray-50 flex items-center justify-between gap-3"
                >
                  <span className="truncate">{`${f.source === 'input' ? '输入' : '输出'} JSON：${f.path}`}</span>
                  <span className="text-xs text-gray-400">添加</span>
                </button>
              ))}
            </div>

            {(selectedExtraFields || []).length > 0 ? (
              <div className="mt-3">
                <div className="text-xs text-gray-500 mb-2">已添加字段列（可移除，不会删除历史值，仅影响展示/导出）</div>
                <div className="flex flex-wrap gap-2">
                  {(selectedExtraFields || []).map((f) => (
                    <button
                      key={`${f.source}.${f.path}`}
                      type="button"
                      onClick={() => removeExtraField(f)}
                      className="px-2 py-1 rounded-lg border border-gray-200 text-xs text-gray-700 hover:bg-gray-50"
                      title="点击移除该字段列"
                    >
                      {`${f.source === 'input' ? '输入' : '输出'}：${f.path}`} ×
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {loading ? <div className="text-sm text-gray-500">正在加载表格数据...</div> : null}

      {!loading && !error && rows.length === 0 ? <div className="text-sm text-gray-500">暂无数据</div> : null}

      {!loading && rows.length > 0 ? (
        <div className="border border-gray-100 rounded-xl overflow-auto bg-white">
          <table className={`${wide ? 'min-w-[1800px]' : 'min-w-[1200px]'} w-full text-sm`}>
            <thead className="bg-gray-50 border-b border-gray-100">
              <tr>
                {(shownColumns || []).map((c) => (
                  <th key={c.key} className="px-3 py-2 text-left text-xs font-semibold text-gray-600 whitespace-nowrap">
                    {c.label}
                  </th>
                ))}
                <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody>
              {(rows || []).map((r) => (
                <tr key={r.item_id} className="border-b border-gray-50 align-top">
                  {(shownColumns || []).map((c) => {
                    const k = c.key;
                    const editable = editableKeys.has(k);
                    const v = r[k];
                    return (
                      <td key={`${r.item_id}-${k}`} className="px-3 py-2">
                        {editable ? (
                          <textarea
                            value={normalizeText(v)}
                            onChange={(e) => handleCellChange(r.item_id, k, e.target.value)}
                            className="w-full min-h-[64px] px-3 py-2 rounded-lg border border-gray-200 bg-white text-sm outline-none focus:ring-2 focus:ring-brand-primary/30 focus:border-brand-primary/40"
                          />
                        ) : (
                          <div
                            className={
                              isLongText(v)
                                ? 'text-gray-800 whitespace-pre-wrap break-words max-h-[160px] overflow-auto pr-2'
                                : 'text-gray-800 whitespace-pre-wrap break-words'
                            }
                          >
                            {normalizeText(v)}
                          </div>
                        )}
                      </td>
                    );
                  })}
                  <td className="px-3 py-2 whitespace-nowrap">
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        disabled={!r.__dirty || r.__saving}
                        isLoading={Boolean(r.__saving)}
                        onClick={() => handleSaveRow(r.item_id)}
                      >
                        保存
                      </Button>
                      {r.__saveError ? <span className="text-xs text-red-600">{r.__saveError}</span> : null}
                      {!r.__saveError && !r.__dirty ? <span className="text-xs text-gray-400">已保存</span> : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
