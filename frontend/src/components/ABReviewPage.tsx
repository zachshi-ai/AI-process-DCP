import { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Button } from './ui/Button';
import { Card } from './ui/Card';
import { Input } from './ui/Input';

type ABRecord = {
  id: string;
  created_at?: string;
  updated_at?: string;
  title?: string;
  skill_name?: string;
  criteria?: string;
  input?: any;
  runs?: { A?: number | null; B?: number | null; J?: number | null };
  note?: string;
  conclusion?: string;
};

type LocalSkill = {
  name: string;
  content: string;
};

const safeJsonParse = (s: string) => {
  const raw = String(s || '').trim();
  if (!raw) return null;
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start >= 0 && end > start) {
    try {
      return JSON.parse(raw.slice(start, end + 1));
    } catch (_) {
      return null;
    }
  }
  try {
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
};

const getWinnerAndConfidence = (judgeText: string) => {
  const obj: any = safeJsonParse(judgeText);
  if (!obj) return { winner: '-', confidence: '-' };
  const winner = String(obj.winner || '-');
  const confidence = typeof obj.confidence === 'number' ? String(obj.confidence) : String(obj.confidence || '-');
  return { winner, confidence };
};

const readFileText = async (file: File) => {
  return await file.text();
};

const buildCustomSkillsFromFolder = async (files: FileList) => {
  const groups: Record<string, Array<{ name: string; content: string }>> = {};
  const fileArr = Array.from(files || []);

  for (const f of fileArr) {
    const rel = String((f as any)?.webkitRelativePath || '');
    const top = rel ? (rel.split('/')[0] || 'skill') : 'skill';
    const fname = f.name || 'file';
    const ext = fname.toLowerCase().split('.').pop() || '';
    if (!['md', 'markdown', 'yaml', 'yml', 'txt'].includes(ext)) continue;
    const content = await readFileText(f);
    if (!groups[top]) groups[top] = [];
    groups[top].push({ name: fname, content });
  }

  const skills: LocalSkill[] = [];
  for (const folder of Object.keys(groups)) {
    const entries = groups[folder] || [];
    const primary =
      entries.find((x) => x.name.toLowerCase() === 'skill.md') ||
      entries.find((x) => x.name.toLowerCase() === 'skill.yaml') ||
      entries.find((x) => x.name.toLowerCase() === 'skill.yml') ||
      entries[0];
    if (!primary) continue;

    const appendix = entries
      .filter((x) => x !== primary)
      .map((x) => `\n\n---\n\n# ${x.name}\n\n${x.content}`.trim())
      .join('\n');
    const merged = `${primary.content}${appendix ? `\n\n${appendix}` : ''}`.trim();
    skills.push({ name: folder, content: merged });
  }

  return skills;
};

const buildJsonInputsFromFiles = async (files: FileList) => {
  const list = Array.from(files || []);
  const items: any[] = [];
  for (const f of list) {
    const name = String(f?.name || 'upload.json');
    let obj: any = null;
    try {
      const text = await readFileText(f);
      obj = JSON.parse(text);
    } catch (_) {
      throw new Error(`JSON 文件解析失败：${name}`);
    }
    const urlFromObj = obj && typeof obj === 'object' ? String((obj as any).url || '').trim() : '';
    const url = urlFromObj || `jsonfile://${name}`;
    items.push({
      url,
      payload: obj,
      meta: { source: 'json_file', filename: name },
    });
  }
  return items;
};

export function ABReviewPage(props: {
  apiBase: string;
  focusRecordId?: string | null;
  onFocusConsumed?: () => void;
}) {
  const apiBase = props.apiBase;
  const skillFolderInputRef = useRef<HTMLInputElement | null>(null);
  const skillFileInputRef = useRef<HTMLInputElement | null>(null);
  const jsonFilesInputRef = useRef<HTMLInputElement | null>(null);
  const [records, setRecords] = useState<ABRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [newTitle, setNewTitle] = useState('');
  const [newSkillName, setNewSkillName] = useState('');
  const [localSkills, setLocalSkills] = useState<LocalSkill[]>([]);
  const [newJsonFiles, setNewJsonFiles] = useState<FileList | null>(null);

  const [judgeCache, setJudgeCache] = useState<Record<string, { winner: string; confidence: string }>>({});
  const [autoJudgeRunning, setAutoJudgeRunning] = useState<Record<string, boolean>>({});

  const focusId = useMemo(() => (props.focusRecordId || '').trim(), [props.focusRecordId]);

  const loadRecords = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await axios.get(`${apiBase}/api/ab/records?limit=200&offset=0`);
      const list = Array.isArray(res.data?.records) ? res.data.records : [];
      setRecords(list);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '加载失败');
    } finally {
      setLoading(false);
    }
  };

  const createRecord = async () => {
    setError('');
    try {
      const customSkillsDict: Record<string, string> = {};
      const sk = localSkills.find((x) => x.name === newSkillName) || localSkills[0];
      if (!sk) throw new Error('请先选择本地 skill（文件或文件夹）');
      setNewSkillName(sk.name);
      customSkillsDict[sk.name] = sk.content;

      if (!newJsonFiles || !newJsonFiles.length) throw new Error('请先选择本地 JSON 文件（可多选）');
      const jsonFromFiles = await buildJsonInputsFromFiles(newJsonFiles);

      const payload: any = {
        title: newTitle,
        skill_name: sk.name,
        urls: [],
        json_inputs: jsonFromFiles,
        custom_skills: Object.keys(customSkillsDict).length ? customSkillsDict : undefined,
      };
      const res = await axios.post(`${apiBase}/api/ab/records`, payload);
      const rec = res.data?.record as ABRecord | undefined;
      setNewTitle('');
      setNewJsonFiles(null);
      await loadRecords();
      if (rec?.id) {
        props.onFocusConsumed?.();
        await startReview(rec.id);
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '创建失败');
    }
  };

  const updateRecord = async (id: string, patch: Partial<ABRecord>) => {
    await axios.post(`${apiBase}/api/ab/records/${encodeURIComponent(id)}/update`, patch);
    await loadRecords();
  };

  const startReview = async (id: string) => {
    setError('');
    try {
      await axios.post(`${apiBase}/api/ab/records/${encodeURIComponent(id)}/run`, { which: 'A' });
      await axios.post(`${apiBase}/api/ab/records/${encodeURIComponent(id)}/run`, { which: 'B' });
      await loadRecords();
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '启动 A/B 失败');
    }
  };

  const pollAndRunJudge = async (record: ABRecord) => {
    const id = record.id;
    const a = record?.runs?.A;
    const b = record?.runs?.B;
    const j = record?.runs?.J;
    if (!a || !b || j) return;
    if (autoJudgeRunning[id]) return;

    setAutoJudgeRunning((prev) => ({ ...prev, [id]: true }));
    try {
      const deadline = Date.now() + 30 * 60 * 1000;
      while (Date.now() < deadline) {
        const [ra, rb] = await Promise.all([
          axios.get(`${apiBase}/api/history/events/${a}`),
          axios.get(`${apiBase}/api/history/events/${b}`),
        ]);
        const sa = String(ra.data?.event?.status || '');
        const sb = String(rb.data?.event?.status || '');
        const doneA = ['completed', 'failed'].includes(sa);
        const doneB = ['completed', 'failed'].includes(sb);
        if (doneA && doneB) break;
        await new Promise((r) => setTimeout(r, 2000));
      }
      await axios.post(`${apiBase}/api/ab/records/${encodeURIComponent(id)}/run`, { which: 'J' });
      await loadRecords();
    } catch (_) {
    } finally {
      setAutoJudgeRunning((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const openHistoryPath = async (eventId: number) => {
    await axios.post(`${apiBase}/api/history/open`, { event_id: eventId });
  };

  const hydrateJudge = async (id: string, eventId: number) => {
    try {
      const res = await axios.get(`${apiBase}/api/history/events/${eventId}`);
      const item = Array.isArray(res.data?.items) ? res.data.items[0] : null;
      const text = String(item?.result || '');
      const { winner, confidence } = getWinnerAndConfidence(text);
      setJudgeCache((prev) => ({ ...prev, [id]: { winner, confidence } }));
    } catch (_) {}
  };

  useEffect(() => {
    if (!apiBase) return;
    loadRecords();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

  useEffect(() => {
    if (!focusId) return;
    const el = document.getElementById(`ab-row-${focusId}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      props.onFocusConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, records.length]);

  useEffect(() => {
    const missing: Array<{ id: string; j?: number | null }> = [];
    for (const r of records) {
      const j = r?.runs?.J;
      if (!j) continue;
      if (!judgeCache[r.id]) missing.push({ id: r.id, j });
    }
    if (!missing.length) return;
    missing.slice(0, 6).forEach(({ id, j }) => {
      if (typeof j === 'number') hydrateJudge(id, j);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [records]);

  useEffect(() => {
    const candidates: ABRecord[] = [];
    for (const r of records) {
      const a = r?.runs?.A;
      const b = r?.runs?.B;
      const j = r?.runs?.J;
      if (!a || !b || j) continue;
      if (autoJudgeRunning[r.id]) continue;
      candidates.push(r);
    }
    if (!candidates.length) return;
    candidates.slice(0, 3).forEach((r) => {
      pollAndRunJudge(r);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [records]);

  return (
    <div className="space-y-8">
      <Card>
        <h2 className="text-2xl font-serif font-bold mb-2">A/B Test</h2>
        <p className="text-sm text-gray-600">
          选择本地 skill + 本地 JSON 后，系统会自动跑 A、B 两个模型；两者完成后自动触发 J（Judge）做对比并输出 winner 与置信度。
        </p>
      </Card>

      <Card>
        <h3 className="text-lg font-semibold mb-4">新建一条评审记录</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Input label="标题" value={newTitle} onChange={(e: any) => setNewTitle(e.target.value)} />
          <div />
        </div>

        <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-semibold text-[#2D3436] mb-1">选择本地 skill 文件夹</label>
              <div className="flex items-center gap-3">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => skillFolderInputRef.current?.click()}
                >
                  选择文件夹
                </Button>
                <span className="text-xs text-gray-500">
                  {localSkills.length ? `已识别 ${localSkills.length} 个 skill` : '未选择'}
                </span>
              </div>
              <input
                ref={skillFolderInputRef}
                type="file"
                className="hidden"
                multiple
                // @ts-ignore
                webkitdirectory="true"
                onChange={async (e: any) => {
                  try {
                    const files: FileList | null = e?.target?.files || null;
                    if (!files || !files.length) return;
                    const skills = await buildCustomSkillsFromFolder(files);
                    setLocalSkills(skills);
                    if (skills.length) setNewSkillName(skills[0].name);
                  } catch (err: any) {
                    setError(err?.message || '解析本地 skill 文件夹失败');
                  }
                }}
              />
              <div className="mt-1 text-xs text-gray-500">建议：文件夹里包含 skill.md（也支持 skill.yaml/yml）</div>
            </div>

            <div>
              <label className="block text-sm font-semibold text-[#2D3436] mb-1">或选择单个 skill 文件</label>
              <div className="flex items-center gap-3">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => skillFileInputRef.current?.click()}
                >
                  选择文件
                </Button>
                <span className="text-xs text-gray-500">
                  {newSkillName ? `当前：${newSkillName}` : '未选择'}
                </span>
              </div>
              <input
                ref={skillFileInputRef}
                type="file"
                className="hidden"
                accept=".md,.markdown,.yaml,.yml,.txt"
                onChange={async (e: any) => {
                  try {
                    const f: File | undefined = e?.target?.files?.[0];
                    if (!f) return;
                    const content = await readFileText(f);
                    const name = f.name.toLowerCase().replace(/\.(md|markdown|yaml|yml|txt)$/i, '') || 'local-skill';
                    const skills = [{ name, content }];
                    setLocalSkills(skills);
                    setNewSkillName(name);
                  } catch (err: any) {
                    setError(err?.message || '读取本地 skill 文件失败');
                  }
                }}
              />
            </div>

            {localSkills.length > 1 && (
              <div>
                <label className="block text-sm font-semibold text-[#2D3436] mb-1">已识别的本地 skill</label>
                <select
                  value={newSkillName}
                  onChange={(e) => setNewSkillName(e.target.value)}
                  className="w-full px-4 py-2.5 rounded-xl border border-gray-200 bg-gray-50/50 text-[#2D3436] focus:bg-white focus:border-[#E8B4B8] focus:ring-4 focus:ring-[#E8B4B8]/20 outline-none"
                >
                  {localSkills.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-semibold text-[#2D3436] mb-1">选择本地 JSON 文件（可多选）</label>
            <div className="flex items-center gap-3">
              <Button
                type="button"
                variant="secondary"
                onClick={() => jsonFilesInputRef.current?.click()}
              >
                选择 JSON 文件
              </Button>
              <span className="text-xs text-gray-500">
                {newJsonFiles?.length ? `已选 ${newJsonFiles.length} 个` : '未选择'}
              </span>
            </div>
            <input
              ref={jsonFilesInputRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              multiple
              onChange={(e: any) => {
                const files: FileList | null = e?.target?.files || null;
                setNewJsonFiles(files && files.length ? files : null);
              }}
            />
            <div className="mt-1 text-xs text-gray-500">
              {newJsonFiles?.length ? `已选择 ${newJsonFiles.length} 个文件（每个文件=1条任务）` : '未选择文件'}
            </div>
          </div>
        </div>

        <div className="mt-5 flex items-center gap-4">
          <Button variant="primary" onClick={createRecord} disabled={loading}>
            开始评审（自动跑 A/B/J）
          </Button>
          <Button variant="secondary" onClick={loadRecords} disabled={loading}>
            刷新
          </Button>
          {loading && <span className="text-sm text-gray-500">加载中...</span>}
          {error && <span className="text-sm text-red-500">{error}</span>}
        </div>
      </Card>

      <Card>
        <h3 className="text-lg font-semibold mb-4">评审记录表</h3>
        <div className="overflow-auto">
          <table className="min-w-[980px] w-full text-sm">
            <thead>
              <tr className="text-left text-gray-600 border-b">
                <th className="py-2 pr-4">时间</th>
                <th className="py-2 pr-4">标题</th>
                <th className="py-2 pr-4">本地Skill</th>
                <th className="py-2 pr-4">JSON数</th>
                <th className="py-2 pr-4">结果</th>
                <th className="py-2 pr-4">备注</th>
                <th className="py-2 pr-4">操作</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => {
                const a = r?.runs?.A;
                const b = r?.runs?.B;
                const j = r?.runs?.J;
                const judge = judgeCache[r.id] || { winner: '-', confidence: '-' };
                const jsonCount = Array.isArray(r?.input?.json_inputs) ? r.input.json_inputs.length : 0;
                return (
                  <tr key={r.id} id={`ab-row-${r.id}`} className="border-b align-top">
                    <td className="py-3 pr-4 whitespace-nowrap text-gray-600">{String(r.created_at || '')}</td>
                    <td className="py-3 pr-4 min-w-[220px]">
                      <div className="font-medium text-gray-900">{String(r.title || '(未命名)')}</div>
                      {focusId === r.id && <div className="text-xs text-brand-primary mt-1">新加入</div>}
                    </td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <div className="font-medium text-gray-900">{String(r.skill_name || '-')}</div>
                      <div className="text-xs text-gray-500 mt-1">本地文件</div>
                    </td>
                    <td className="py-3 pr-4 whitespace-nowrap text-gray-600">{jsonCount}</td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <div className="font-medium text-gray-900">{judge.winner}</div>
                      <div className="text-gray-600">置信度：{judge.confidence}</div>
                    </td>
                    <td className="py-3 pr-4 min-w-[240px]">
                      <textarea
                        className="w-full border rounded-lg px-3 py-2 text-sm"
                        rows={3}
                        value={String(r.note || '')}
                        onChange={(e) => {
                          const v = e.target.value;
                          setRecords((prev) => prev.map((x) => (x.id === r.id ? { ...x, note: v } : x)));
                        }}
                        onBlur={async () => {
                          try {
                            await updateRecord(r.id, { note: r.note || '' });
                          } catch (_) {}
                        }}
                      />
                    </td>
                    <td className="py-3 pr-4 min-w-[260px]">
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="secondary"
                          className="px-3 py-1.5 text-xs rounded-lg"
                          onClick={() => startReview(r.id)}
                        >
                          重新评审
                        </Button>
                        {typeof a === 'number' && (
                          <Button variant="ghost" className="px-3 py-1.5 text-xs rounded-lg" onClick={() => openHistoryPath(a)}>
                            打开A输出
                          </Button>
                        )}
                        {typeof b === 'number' && (
                          <Button variant="ghost" className="px-3 py-1.5 text-xs rounded-lg" onClick={() => openHistoryPath(b)}>
                            打开B输出
                          </Button>
                        )}
                        {typeof j === 'number' && (
                          <Button variant="ghost" className="px-3 py-1.5 text-xs rounded-lg" onClick={() => openHistoryPath(j)}>
                            打开评审结果
                          </Button>
                        )}
                        {typeof a === 'number' && typeof b === 'number' && !j && (
                          <span className="text-xs text-gray-500 px-2 py-1">等待 A/B 完成后自动评审…</span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!records.length && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-gray-500">
                    暂无记录
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
