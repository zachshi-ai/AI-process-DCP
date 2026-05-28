import { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';

type RiskLevel = 'high' | 'mid' | 'low';
type SuggestKind = 'pass' | 'reject' | 'manual';
type SuggestFilter = 'all' | SuggestKind;
type SortKey = 'risk' | 'time_desc' | 'time_asc' | 'amount_desc' | 'amount_asc';

type Decision = 'approved' | 'rejected' | 'transferred';

type RiskItem = {
  id: string;
  level: RiskLevel;
  title: string;
  description: string;
};

type DemoItem = {
  id: string;
  title: string;
  applicantName: string;
  applicantDept: string;
  amountCny: number | null;
  createdAtISO: string;
  riskLevel: RiskLevel;
  aiSummary: string;
  preApprovalSuggestion: string;
  riskItems: RiskItem[];
  riskSummary: string;
  status: 'todo' | 'done';
  decision?: Decision;
  decidedAtISO?: string;
  url?: string;
};

const toText = (v: any) => {
  if (v == null) return '';
  return String(v);
};

const stripFences = (s: string) => {
  let out = (s || '').trim();
  if (!out) return '';
  if (out.includes('```')) {
    out = out.replace(/```json/gi, '```').replace(/```/g, '').trim();
  }
  return out;
};

const safeJsonParse = (s: string) => {
  const raw = String(s || '').trim();
  if (!raw) return null;

  const tryParse = (candidate: string) => {
    const clean = stripFences(candidate);
    if (!clean) return null;
    try {
      return JSON.parse(clean);
    } catch (_) {
      return null;
    }
  };

  const direct = tryParse(raw);
  if (direct != null) return direct;

  const matches = raw.matchAll(/```(?:json|JSON)?\s*([\s\S]*?)```/g);
  for (const m of matches) {
    const inner = m[1] || '';
    const parsed = tryParse(inner);
    if (parsed != null) return parsed;
  }

  const firstObj = raw.indexOf('{');
  const lastObj = raw.lastIndexOf('}');
  if (firstObj !== -1 && lastObj !== -1 && lastObj > firstObj) {
    const parsed = tryParse(raw.slice(firstObj, lastObj + 1));
    if (parsed != null) return parsed;
  }
  const firstArr = raw.indexOf('[');
  const lastArr = raw.lastIndexOf(']');
  if (firstArr !== -1 && lastArr !== -1 && lastArr > firstArr) {
    const parsed = tryParse(raw.slice(firstArr, lastArr + 1));
    if (parsed != null) return parsed;
  }

  return null;
};

const humanizeLine = (raw: string) => {
  let s = (raw || '').trim();
  if (!s) return '';
  s = s.replace(/^[\s\u3000]*风险点[0-9一二三四五六七八九十]+[：:\-]\s*/g, '');
  s = s.replace(/^[\s\u3000]*风险依据[0-9一二三四五六七八九十]+[：:\-]\s*/g, '');
  s = s.replace(/^[\s\u3000]*([0-9]+(\.[0-9]+)*)\s*/g, '');
  s = s.replace(/^Skill[\s\u3000]*核心规则[0-9.\-：:]*\s*/g, '');
  s = s.replace(/^执行纪律[：:]\s*/g, '');
  s = s.replace(/=高风险/g, '会直接判为高风险');
  s = s.replace(/=中风险/g, '会判为中风险');
  s = s.replace(/=低风险/g, '会判为低风险');
  s = s.replace(/数据缺失/g, '关键数据缺失');
  s = s.replace(/下钻失败/g, '下钻读取失败');
  return s.trim();
};

const normalizeRiskLevel = (raw: any): RiskLevel => {
  const s = toText(raw).trim();
  if (s === '高' || /high/i.test(s) || s.includes('高风险')) return 'high';
  if (s === '中' || /mid/i.test(s) || s.includes('中风险')) return 'mid';
  if (s === '低' || /low/i.test(s) || s.includes('低风险')) return 'low';
  return 'mid';
};

const summarizeRiskTitle = (raw: string) => {
  let cleaned = humanizeLine(toText(raw));
  if (!cleaned) return '风险项';

  cleaned = cleaned
    .replace(/在表单数据中/g, '')
    .replace(/在表单中/g, '')
    .replace(/在表单里/g, '')
    .replace(/在表单数据里/g, '')
    .replace(/存在合规风险/g, '')
    .trim();

  const beforeComma = cleaned.split(/[。；;，,]/)[0].trim();
  let base = beforeComma || cleaned;

  base = base.replace(/[“”"'][^“”"']{0,60}[“”"']/g, '').trim();

  const m = base.match(/^(.{1,18}?)(缺失|未抓取到|未获取到|未确认|未查验|不一致|异常|失败|为空)/);
  if (m) base = `${m[1]}${m[2]}`.trim();

  if (base.length <= 10) return base;
  return `${base.slice(0, 10)}…`;
};

const normalizeSuggestion = (s: string, riskLevel: RiskLevel) => {
  let out = (s || '建议复核关键信息后决策').replace(/复核金额偏差后/g, '注意符合金额偏差').replace(/立即处理/g, 'AI 风险阻断：');
  if (riskLevel === 'high') out = out.replace(/转人工核验/g, '重点核验').replace(/转人工/g, '重点核验');
  out = out.replace(/建议通过/g, 'AI 建议通过').replace(/建议驳回/g, 'AI 建议驳回');
  out = out.replace(/AI\s+AI/g, 'AI');
  return out;
};

const suggestionKind = (it: DemoItem): SuggestKind => {
  const s = normalizeSuggestion(it.preApprovalSuggestion || '', it.riskLevel);
  if (/^AI\s*建议通过/.test(s)) return 'pass';
  if (/^AI\s*建议驳回/.test(s)) return 'reject';
  return 'manual';
};

const tokenizeSuggestion = (s: string) => {
  const match = s.match(/^([^(（]+)(?:[((（](.*)[))）])?$/);
  if (match) {
    const mainSuggestion = (match[1] || '').trim();
    const details = match[2] ? (match[2] || '').trim() : '';
    let tone: '' | 'approve' | 'reject' | 'immediate' = '';
    if (mainSuggestion.includes('AI 建议通过')) tone = 'approve';
    else if (mainSuggestion.includes('AI 建议驳回')) tone = 'reject';
    else if (mainSuggestion.includes('AI 风险阻断')) tone = 'immediate';
    const tokens: { t: string; tone?: string }[] = [];
    if (tone) tokens.push({ t: mainSuggestion, tone });
    else tokens.push({ t: mainSuggestion });
    if (details) tokens.push({ t: ` （${details}）` });
    return tokens;
  }
  return [{ t: String(s) }];
};

const fmtCny = (n: any) => {
  if (n == null || n === '') return '';
  try {
    return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', maximumFractionDigits: 0 }).format(Number(n));
  } catch {
    return `¥${String(n)}`;
  }
};

const fmtTime = (iso: string) => {
  const d = new Date(iso);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}/${dd} ${hh}:${mi}`;
};

const parseNumberLike = (raw: any) => {
  const s = toText(raw).trim();
  if (!s) return null;
  const cleaned = s.replace(/[,，\s]/g, '');
  const m = cleaned.match(/-?\d+(\.\d+)?/);
  if (!m) return null;
  const v = Number(m[0]);
  return Number.isFinite(v) ? v : null;
};

const pickField = (obj: any, keys: string[]) => {
  for (const k of keys) {
    const v = obj?.[k];
    const s = toText(v).trim();
    if (s) return s;
  }
  return '';
};

const pickNumberField = (obj: any, keys: string[]) => {
  for (const k of keys) {
    const v = parseNumberLike(obj?.[k]);
    if (v != null) return v;
  }
  return null;
};

const parseFlowObj = (raw: any) => {
  if (!raw) return null;
  if (typeof raw === 'object') return raw;
  const s = toText(raw).trim();
  if (!s) return null;
  const j = safeJsonParse(s);
  if (j && typeof j === 'object') return j;
  return null;
};

const deriveDemoItem = (historyItem: any): DemoItem => {
  const id = String(historyItem?.id ?? historyItem?.ts ?? Math.random());
  const createdAtISO = historyItem?.ts ? new Date(Number(historyItem.ts) * 1000).toISOString() : new Date().toISOString();
  const url = toText(historyItem?.url).trim();
  const meta = (historyItem?.meta && typeof historyItem.meta === 'object') ? historyItem.meta : {};
  const extractedKv = (meta?.extracted_kv && typeof meta.extracted_kv === 'object') ? meta.extracted_kv : {};

  const parsedResult = typeof historyItem?.result === 'string' ? safeJsonParse(historyItem.result) : null;
  const llmRaw = parsedResult?.llm_raw ?? historyItem?.raw ?? '';
  const llmCandidate = parsedResult?.llm_result ?? historyItem?.llm_result ?? historyItem?.result;
  const llm = llmCandidate ?? llmRaw;
  const flowObj = parseFlowObj(llm) || {};
  const llmText = typeof llm === 'string' ? toText(llm).trim() : '';
  const isStructured = Object.keys(flowObj || {}).length > 0;

  const flowName =
    pickField(flowObj, ['流程名称', 'flow_name', 'name']) ||
    pickField(extractedKv, ['流程名称', '流程', '流程类型', '表单类型', '标题']) ||
    (isStructured ? '未识别流程名称' : '未结构化输出');

  const flowContent =
    pickField(flowObj, ['流程内容', '流程主题', '事项', 'title']) ||
    pickField(extractedKv, ['付款事由', '用印文件', '合同名称', '采购名称', '报销事由', '来访事由', '请假事由', '事项', '主题', 'KOA单据编号', '单据编码', '标题']) ||
    '';
  const riskLevelText =
    pickField(flowObj, ['风险等级', '风险级别', 'risk_level', 'riskLevel']) ||
    pickField(extractedKv, ['风险等级', '风险级别', '风险']) ||
    '';
  const riskLevel = normalizeRiskLevel(riskLevelText);

  const suggestionRaw = pickField(flowObj, ['流程审批建议', 'approval_suggestion', 'suggestion']);
  const suggestionText = riskLevel === 'high'
    ? 'AI 风险阻断（重点核验）'
    : suggestionRaw.includes('通过')
      ? 'AI 建议通过'
      : suggestionRaw.includes('驳回') || suggestionRaw.includes('拒绝')
        ? 'AI 建议驳回'
        : '建议复核关键信息后决策';

  const applicantName =
    pickField(flowObj, ['申请人', '报销人', '邀约人', '发起人']) ||
    pickField(extractedKv, ['申请人', '报销人', '邀约人', '发起人', '员工姓名']) ||
    '—';

  const applicantDept =
    pickField(flowObj, ['申请部门', '报销部门', '邀约部门', '发起部门']) ||
    pickField(extractedKv, ['申请人部门', '申请部门', '报销部门', '邀约部门', '发起部门']) ||
    '—';
  const amountCny =
    pickNumberField(flowObj, ['付款金额', '实际报销金额', '合同金额', '采购金额', '报销金额', '金额', '申请金额']);
  const amountCny2 =
    amountCny != null
      ? amountCny
      : pickNumberField(extractedKv, ['付款金额', '实际报销金额', '实际报销金额合计', '合同金额', '采购金额', '报销金额', '金额', '申请金额', '对账金额合计', '实际支付金额合计']);

  const riskPointsRaw = flowObj?.['风险点'] ?? flowObj?.risk_points ?? flowObj?.riskPoints ?? [];
  const riskPointsOriginal: string[] = Array.isArray(riskPointsRaw) ? riskPointsRaw.map((x: any) => toText(x).trim()).filter(Boolean) : [];
  const riskPoints: string[] = Array.isArray(riskPointsRaw) ? riskPointsRaw.map((x: any) => humanizeLine(toText(x))).filter(Boolean) : [];

  const riskItems: RiskItem[] = (riskPoints.length ? riskPoints : ['无风险项']).slice(0, 8).map((p, idx) => {
    const original = riskPointsOriginal[idx] || p;
    const title = summarizeRiskTitle(p);
    const description = original || (p.length > 18 ? p : '请人工核验关键字段与附件材料。');
    return { id: `${id}-r${idx}`, level: riskLevel, title, description };
  });

  const riskSummary =
    pickField(flowObj, ['风险总结', 'risk_summary', 'riskSummary', '风险摘要', 'summary']) ||
    riskPoints.join('；') ||
    '无明显风险项。';

  const aiSummaryLines: string[] = [];
  if (applicantName !== '—') aiSummaryLines.push(`申请人: ${applicantName}`);
  if (applicantDept !== '—') aiSummaryLines.push(`申请部门: ${applicantDept}`);
  if (amountCny2 != null) aiSummaryLines.push(`金额: ${fmtCny(amountCny2)}`);
  if (url) aiSummaryLines.push(`来源URL: ${url}`);
  if (!isStructured && llmText) {
    const preview = llmText.length > 160 ? `${llmText.slice(0, 160)}…` : llmText;
    aiSummaryLines.push(`LLM输出: ${preview.replace(/\n+/g, ' ')}`);
  }

  return {
    id,
    title: flowContent ? `${flowName} · ${flowContent}` : flowName,
    applicantName,
    applicantDept,
    amountCny: amountCny2,
    createdAtISO,
    riskLevel,
    aiSummary: aiSummaryLines.join('\n'),
    preApprovalSuggestion: suggestionText,
    riskItems,
    riskSummary,
    status: 'todo',
    url,
  };
};

export function PreApprovalDemoPage({ apiBase }: { apiBase: string }) {
  const [events, setEvents] = useState<any[]>([]);
  const [eventId, setEventId] = useState<number | null>(null);
  const [historyItems, setHistoryItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const [tab, setTab] = useState<'todo' | 'done'>('todo');
  const [risk, setRisk] = useState<'all' | RiskLevel>('all');
  const [suggest, setSuggest] = useState<SuggestFilter>('all');
  const [sort, setSort] = useState<SortKey>('risk');
  const [q, setQ] = useState('');
  const [expandedRisk, setExpandedRisk] = useState<Set<string>>(new Set());
  const [expandedAi, setExpandedAi] = useState<Set<string>>(new Set());
  const [batchMode, setBatchMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortOpen, setSortOpen] = useState(false);
  const [toastMsg, setToastMsg] = useState('');
  const toastT = useRef<number | null>(null);

  const demoItems = useMemo(() => {
    return (historyItems || []).map(deriveDemoItem);
  }, [historyItems]);

  const [items, setItems] = useState<DemoItem[]>([]);
  useEffect(() => {
    setItems(demoItems);
    setTab('todo');
    setRisk('all');
    setSuggest('all');
    setSort('risk');
    setQ('');
    setExpandedAi(new Set());
    setExpandedRisk(new Set());
    setBatchMode(false);
    setSelected(new Set());
    setSortOpen(false);
  }, [demoItems]);

  const toast = (msg: string) => {
    setToastMsg(msg);
    if (toastT.current) window.clearTimeout(toastT.current);
    toastT.current = window.setTimeout(() => setToastMsg(''), 1400);
  };

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setErr('');
      try {
        const res = await axios.get(`${apiBase}/api/history/events`, { params: { limit: 30, offset: 0 } });
        const list = Array.isArray(res?.data?.events) ? res.data.events : [];
        if (!mounted) return;
        setEvents(list);
        const first = list[0];
        if (first?.id) setEventId(Number(first.id));
      } catch (e: any) {
        if (!mounted) return;
        setErr(e?.response?.data?.detail || e?.message || '加载历史记录失败');
      } finally {
        if (!mounted) return;
        setLoading(false);
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, [apiBase]);

  useEffect(() => {
    if (!eventId) return;
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setErr('');
      try {
        const res = await axios.get(`${apiBase}/api/history/events/${eventId}`);
        const list = Array.isArray(res?.data?.items) ? res.data.items : [];
        if (!mounted) return;
        setHistoryItems(list);
      } catch (e: any) {
        if (!mounted) return;
        setErr(e?.response?.data?.detail || e?.message || '加载详情失败');
      } finally {
        if (!mounted) return;
        setLoading(false);
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, [apiBase, eventId]);

  const confirmYesNo = (msg: string) => window.confirm(msg);

  const decide = (id: string, d: Decision) => {
    setItems((prev) => {
      const out = prev.map((x) => {
        if (x.id !== id) return x;
        return { ...x, status: 'done' as const, decision: d, decidedAtISO: new Date().toISOString() };
      });
      return out;
    });
    toast(d === 'approved' ? '已通过' : '已驳回');
  };

  const toggleExpand = (kind: 'risk' | 'ai', id: string) => {
    if (kind === 'risk') {
      setExpandedRisk((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      return;
    }
    setExpandedAi((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const matchesSearch = (it: DemoItem, q0: string) => {
    const needle = String(q0 || '').trim().toLowerCase();
    if (!needle) return true;
    const hay: any[] = [];
    hay.push(it.title, it.applicantName, it.applicantDept);
    hay.push(it.preApprovalSuggestion);
    hay.push(it.aiSummary);
    hay.push(it.riskSummary);
    if (it.amountCny != null) hay.push(String(it.amountCny), fmtCny(it.amountCny));
    if (Array.isArray(it.riskItems)) {
      for (const ri of it.riskItems) hay.push(ri.title, ri.description);
    }
    return hay.filter(Boolean).some((s) => String(s).toLowerCase().includes(needle));
  };

  const applySort = (list: DemoItem[]) => {
    const byRisk = (x: DemoItem) => (x.riskLevel === 'high' ? 3 : x.riskLevel === 'mid' ? 2 : 1);
    const byTime = (x: DemoItem) => new Date(x.createdAtISO).getTime();
    const byAmount = (x: DemoItem) => (x.amountCny == null ? -1 : Number(x.amountCny));
    const k = sort;
    const out = Array.from(list);
    out.sort((a, b) => {
      if (k === 'risk') {
        const riskDiff = byRisk(b) - byRisk(a);
        return riskDiff !== 0 ? riskDiff : byTime(b) - byTime(a);
      }
      if (k === 'time_desc') return byTime(b) - byTime(a);
      if (k === 'time_asc') return byTime(a) - byTime(b);
      if (k === 'amount_desc') return byAmount(b) - byAmount(a);
      if (k === 'amount_asc') return byAmount(a) - byAmount(b);
      return 0;
    });
    return out;
  };

  const sortText = (k: SortKey) => {
    if (k === 'risk') return '风险优先';
    if (k === 'time_desc') return tab === 'done' ? '新审批在前' : '刚发起的在前';
    if (k === 'time_asc') return tab === 'done' ? '旧审批在前' : '积压最久的在前';
    if (k === 'amount_desc') return '金额高';
    return '金额低';
  };

  const setTabSafe = (t: 'todo' | 'done') => {
    setTab(t);
    setRisk('all');
    setSuggest('all');
    setSort('risk');
    setQ('');
    setExpandedAi(new Set());
    setExpandedRisk(new Set());
    setBatchMode(false);
    setSelected(new Set());
  };

  const toggleRiskFilter = (level: RiskLevel) => {
    if (suggest !== 'all') setSuggest('all');
    setRisk((prev) => (prev === level ? 'all' : level));
  };

  const setSuggestFilter = (k: SuggestFilter) => {
    if (risk !== 'all') setRisk('all');
    setSuggest((prev) => (prev === k ? 'all' : k));
  };

  const counts = useMemo(() => {
    const tabCnt = { todo: 0, done: 0 };
    const riskCnt: Record<RiskLevel, number> = { high: 0, mid: 0, low: 0 };
    const sugCnt: Record<SuggestKind, number> = { pass: 0, reject: 0, manual: 0 };

    for (const it of items) {
      if (it.status === 'todo') tabCnt.todo += 1;
      if (it.status === 'done') tabCnt.done += 1;

      if (it.status !== tab) continue;
      if (!matchesSearch(it, q)) continue;
      const k: SuggestKind = suggestionKind(it);

      if (suggest === 'all' || k === suggest) {
        riskCnt[it.riskLevel] += 1;
      }
      if (risk === 'all' || it.riskLevel === risk) sugCnt[k] += 1;
    }
    return { tabCnt, riskCnt, sugCnt };
  }, [items, tab, risk, suggest, q]);

  const visibleList = useMemo(() => {
    let filtered = items.filter((x) => x.status === tab);
    filtered = filtered.filter((x) => matchesSearch(x, q));
    if (risk !== 'all') filtered = filtered.filter((x) => x.riskLevel === risk);
    if (suggest !== 'all') filtered = filtered.filter((x) => suggestionKind(x) === suggest);
    return applySort(filtered);
  }, [items, tab, risk, suggest, q, sort]);

  const onBatchToggle = () => {
    setBatchMode((prev) => !prev);
    setSelected(new Set());
  };

  const onToggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const batchApprove = () => {
    if (selected.size === 0) return;
    if (!confirmYesNo(`确认批量同意选中的 ${selected.size} 个审批吗？`)) return;
    const now = new Date().toISOString();
    let processed = 0;
    setItems((prev) =>
      prev.map((it) => {
        if (!selected.has(it.id) || it.status === 'done') return it;
        processed += 1;
        return { ...it, status: 'done' as const, decision: 'approved', decidedAtISO: now };
      })
    );
    setBatchMode(false);
    setSelected(new Set());
    toast(`已批量同意 ${processed} 条`);
  };

  const batchReject = () => {
    if (selected.size === 0) return;
    if (!confirmYesNo(`确认批量驳回选中的 ${selected.size} 个审批吗？`)) return;
    const now = new Date().toISOString();
    let processed = 0;
    setItems((prev) =>
      prev.map((it) => {
        if (!selected.has(it.id) || it.status === 'done') return it;
        processed += 1;
        return { ...it, status: 'done' as const, decision: 'rejected', decidedAtISO: now };
      })
    );
    setBatchMode(false);
    setSelected(new Set());
    toast(`已批量驳回 ${processed} 条`);
  };

  const batchAI = () => {
    if (selected.size === 0) return;
    let actionableCount = 0;
    for (const id of selected) {
      const it = items.find((x) => x.id === id);
      if (!it || it.status === 'done') continue;
      const sugText = it.preApprovalSuggestion || '';
      if (sugText.includes('AI 建议通过') || sugText.includes('AI 建议驳回')) actionableCount += 1;
    }
    if (actionableCount === 0) {
      toast('当前选中的审批中没有可由 AI 自动处理的明确建议');
      return;
    }
    if (!confirmYesNo(`确认按照 AI 建议批量处理选中的审批吗？\n（将自动处理 ${actionableCount} 条明确同意/驳回的审批，跳过 ${selected.size - actionableCount} 条需人工核验的审批）`)) return;
    const now = new Date().toISOString();
    let processed = 0;
    let skipped = 0;
    setItems((prev) =>
      prev.map((it) => {
        if (!selected.has(it.id) || it.status === 'done') return it;
        const sugText = it.preApprovalSuggestion || '';
        if (sugText.includes('AI 建议通过')) {
          processed += 1;
          return { ...it, status: 'done' as const, decision: 'approved', decidedAtISO: now };
        }
        if (sugText.includes('AI 建议驳回')) {
          processed += 1;
          return { ...it, status: 'done' as const, decision: 'rejected', decidedAtISO: now };
        }
        skipped += 1;
        return it;
      })
    );
    setBatchMode(false);
    setSelected(new Set());
    toast(`已根据 AI 建议处理 ${processed} 条` + (skipped > 0 ? `，跳过 ${skipped} 条需人工核验` : ''));
  };

  const allApprove = () => {
    let list = items.filter((x) => x.status === 'todo');
    if (risk !== 'all') list = list.filter((x) => x.riskLevel === risk);
    if (list.length === 0) return;
    if (!confirmYesNo(`确认将当前列表中的 ${list.length} 个待办全部同意吗？`)) return;
    const now = new Date().toISOString();
    let processed = 0;
    setItems((prev) =>
      prev.map((it) => {
        if (it.status !== 'todo') return it;
        if (risk !== 'all' && it.riskLevel !== risk) return it;
        processed += 1;
        return { ...it, status: 'done' as const, decision: 'approved', decidedAtISO: now };
      })
    );
    toast(`已全部同意 ${processed} 条`);
  };

  const allReject = () => {
    let list = items.filter((x) => x.status === 'todo');
    if (risk !== 'all') list = list.filter((x) => x.riskLevel === risk);
    if (list.length === 0) return;
    if (!confirmYesNo(`确认将当前列表中的 ${list.length} 个待办全部驳回吗？`)) return;
    const now = new Date().toISOString();
    let processed = 0;
    setItems((prev) =>
      prev.map((it) => {
        if (it.status !== 'todo') return it;
        if (risk !== 'all' && it.riskLevel !== risk) return it;
        processed += 1;
        return { ...it, status: 'done' as const, decision: 'rejected', decidedAtISO: now };
      })
    );
    toast(`已全部驳回 ${processed} 条`);
  };

  const allAI = () => {
    let list = items.filter((x) => x.status === 'todo');
    if (risk !== 'all') list = list.filter((x) => x.riskLevel === risk);
    if (list.length === 0) return;
    let actionableCount = 0;
    for (const it of list) {
      const sugText = it.preApprovalSuggestion || '';
      if (sugText.includes('AI 建议通过') || sugText.includes('AI 建议驳回')) actionableCount += 1;
    }
    if (actionableCount === 0) {
      toast('当前列表中没有可由 AI 自动处理的明确建议');
      return;
    }
    if (!confirmYesNo(`确认按照 AI 建议将当前列表中的待办进行处理吗？\n（将自动处理 ${actionableCount} 条明确同意/驳回的审批，跳过 ${list.length - actionableCount} 条需人工核验的审批）`)) return;
    const now = new Date().toISOString();
    let processed = 0;
    let skipped = 0;
    setItems((prev) =>
      prev.map((it) => {
        if (it.status !== 'todo') return it;
        if (risk !== 'all' && it.riskLevel !== risk) return it;
        const sugText = it.preApprovalSuggestion || '';
        if (sugText.includes('AI 建议通过')) {
          processed += 1;
          return { ...it, status: 'done' as const, decision: 'approved', decidedAtISO: now };
        }
        if (sugText.includes('AI 建议驳回')) {
          processed += 1;
          return { ...it, status: 'done' as const, decision: 'rejected', decidedAtISO: now };
        }
        skipped += 1;
        return it;
      })
    );
    toast(`已根据 AI 建议处理 ${processed} 条` + (skipped > 0 ? `，跳过 ${skipped} 条需人工核验` : ''));
  };

  const css = `
      :root {
        --c-primary: #3370ff;
        --c-primary-hover: #2a5de6;
        --c-bg: #f8fafc;
        --c-card: rgba(255, 255, 255, 0.7);
        --c-text-1: #0f172a;
        --c-text-2: #475569;
        --c-text-3: #64748b;
        --c-border: rgba(255, 255, 255, 0.8);
        --c-border-solid: #e2e8f0;
        --c-primary-weak: rgba(51, 112, 255, 0.08);

        --c-risk-h: #ef4444;
        --c-risk-m: #f59e0b;
        --c-risk-l: #10b981;
        
        --radius: 16px;
        --shadow-sm: 0 2px 8px -2px rgba(15, 23, 42, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.6);
        --shadow: 0 8px 24px -6px rgba(15, 23, 42, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.8);
        --shadow-lg: 0 16px 40px -8px rgba(15, 23, 42, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.9);
        --glass-blur: blur(24px);

        --font: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'SF Pro Display',
          'PingFang SC', 'Hiragino Sans GB', Roboto, 'Noto Sans SC', system-ui, sans-serif;
      }

      .pre-demo-host {
        margin: 0;
        background: #e2e8f0;
        font-family: var(--font);
        font-size: 15px;
        color: var(--c-text-1);
        text-rendering: optimizeLegibility;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
        display: flex;
        justify-content: center;
        align-items: center;
        min-height: 100svh;
        position: relative;
      }

      .pre-demo-host * { box-sizing: border-box; }

      .app-shell {
        width: 100%;
        max-width: 393px;
        height: 100svh;
        max-height: 852px;
        margin: 0 auto;
        background-color: #f8fafc;
        background-image: 
          radial-gradient(at 0% 0%, hsla(210, 100%, 96%, 1) 0px, transparent 50%),
          radial-gradient(at 100% 0%, hsla(250, 100%, 97%, 1) 0px, transparent 50%),
          radial-gradient(at 100% 100%, hsla(210, 100%, 96%, 1) 0px, transparent 50%),
          radial-gradient(at 0% 100%, hsla(200, 40%, 97%, 1) 0px, transparent 50%);
        display: flex;
        flex-direction: column;
        position: relative;
      }

      .app-shell::before, .app-shell::after {
        content: '';
        position: absolute;
        border-radius: 50%;
        filter: blur(80px);
        z-index: 0;
        pointer-events: none;
        animation: floatOrb 12s ease-in-out infinite alternate;
      }
      .app-shell::before {
        width: 300px; height: 300px;
        background: rgba(56, 189, 248, 0.15);
        top: -50px; left: -100px;
      }
      .app-shell::after {
        width: 350px; height: 350px;
        background: rgba(167, 139, 250, 0.1);
        bottom: 100px; right: -100px;
        animation-delay: -6s;
      }
      @keyframes floatOrb {
        0% { transform: translate(0, 0) scale(1); }
        100% { transform: translate(40px, 60px) scale(1.05); }
      }

      @media (min-width: 480px) {
        .pre-demo-host { perspective: 1000px; }
        .app-shell {
          border-radius: 40px;
          box-shadow: 0 24px 60px rgba(15, 23, 42, 0.1), 0 0 0 8px rgba(255, 255, 255, 0.6);
          overflow: hidden;
          margin: auto;
          height: 852px;
          min-height: auto;
          transform: scale(min(1, calc((100svh - 80px) / 852)));
          transform-origin: center center;
          border: 1px solid rgba(0, 0, 0, 0.05);
        }
      }

      @media (min-width: 768px) {
        .app-shell { max-width: 393px; }
      }

      .fixed-header {
        flex: none;
        position: sticky;
        top: 0;
        z-index: 10;
        background: rgba(255, 255, 255, 0.6);
        backdrop-filter: var(--glass-blur);
        -webkit-backdrop-filter: var(--glass-blur);
        border-bottom: 1px solid rgba(255, 255, 255, 0.9);
      }

      .top-hero { padding-top: max(env(safe-area-inset-top), 24px); }
      .top-hero-inner { width: 100%; max-width: 393px; margin: 0 auto; position: relative; z-index: 2; }
      
      .van-nav-bar { height: 46px; display: flex; align-items: center; justify-content: center; position: relative; background: transparent; }
      .van-nav-bar__title { font-size: 17px; font-weight: 800; color: var(--c-text-1); letter-spacing: -0.3px; }

      .top-tabs {
        width: 100%; max-width: 393px; margin: 0 auto;
        padding: 4px 16px 8px; display: flex; align-items: center; gap: 10px;
        position: relative; z-index: 2;
      }
      .tab-group { flex: 1; display: flex; background: rgba(15, 23, 42, 0.04); border-radius: 12px; padding: 4px; box-shadow: inset 0 1px 3px rgba(0,0,0,0.02); }
      .tab {
        flex: 1; border: none; background: transparent;
        border-radius: 10px; height: 32px; font-size: 14px; font-weight: 600; color: var(--c-text-2); cursor: pointer; outline: none; transition: 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
      }
      .tab.active { background: #ffffff; color: var(--c-primary); box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08); font-weight: 700; }

      .tab-count {
        height: 18px;
        padding: 0 8px;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.06);
        color: var(--c-text-2);
        font-size: 12px;
        font-weight: 800;
        line-height: 18px;
      }
      .tab.active .tab-count {
        background: rgba(51, 112, 255, 0.12);
        color: var(--c-primary);
      }

      .page-header { padding: 8px 16px 0; position: relative; z-index: 2; }
      .scroll-area { flex: 1; overflow-y: auto; padding: 12px 16px calc(24px + env(safe-area-inset-bottom)); -webkit-overflow-scrolling: touch; position: relative; z-index: 1; }

      .panel { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-card); backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur); box-shadow: var(--shadow); overflow: hidden; }
      .row { display: flex; align-items: stretch; padding: 6px 6px; }
      .divider { width: 1px; background: rgba(15,23,42,0.06); margin: 4px 2px; }
      .divider-h { height: 1px; background: rgba(15,23,42,0.06); margin: 0 10px; }
      .seg { flex: 1; border: 0; background: transparent; padding: 8px; text-align: left; cursor: pointer; border-radius: 10px; transition: 0.2s; outline: none; }
      .seg:active { opacity: 0.7; }
      .seg.active { background: rgba(255,255,255,0.8); box-shadow: var(--shadow-sm); }
      .pair { display: flex; align-items: baseline; justify-content: space-between; gap: 6px; }
      .label { font-size: 14px; color: var(--c-text-2); font-weight: 800; }
      .value { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
      .seg[data-level='high'] .value { color: var(--c-risk-h); }
      .seg[data-level='mid'] .value { color: var(--c-risk-m); }
      .seg[data-level='low'] .value { color: var(--c-risk-l); }

      .toolbar { margin-top: 16px; margin-bottom: 4px; display: flex; align-items: center; justify-content: space-between; position: relative; }
      .toolbar-row { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; }
      
      .van-dropdown-menu__bar { height: 32px; padding: 0 12px; border-radius: 999px; background: var(--c-card); backdrop-filter: var(--glass-blur); border: 1px solid var(--c-border); display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: var(--shadow-sm); transition: 0.2s; }
      .van-dropdown-menu__bar:active { background: rgba(255,255,255,0.9); }
      .van-dropdown-menu__title { font-size: 13px; font-weight: 700; color: var(--c-text-1); display: flex; align-items: center; }
      .van-dropdown-menu__title::after { content: ''; margin-left: 6px; border: 4px solid transparent; border-top-color: var(--c-text-3); transform-origin: center; transition: transform 0.2s; margin-top: 4px; }
      .van-dropdown-menu__title.active::after { transform: rotate(180deg); margin-top: -4px; border-top-color: var(--c-text-1); }
      
      .batch-trigger-btn {
        height: 32px; padding: 0 14px; border-radius: 999px; background: var(--c-card); backdrop-filter: var(--glass-blur); border: 1px solid var(--c-border); display: flex; align-items: center; justify-content: center; cursor: pointer; color: var(--c-text-1); font-weight: 700; font-size: 13px; box-shadow: var(--shadow-sm); transition: 0.2s;
      }
      .batch-trigger-btn.active { background: var(--c-primary); color: #fff; border-color: var(--c-primary); box-shadow: 0 4px 12px rgba(51,112,255,0.2); }

      .search {
        flex: 1;
        min-width: 0;
        height: 32px;
        padding: 0 12px;
        border-radius: 999px;
        border: 1px solid var(--c-border);
        background: var(--c-card);
        backdrop-filter: var(--glass-blur);
        -webkit-backdrop-filter: var(--glass-blur);
        box-shadow: var(--shadow-sm);
        color: var(--c-text-1);
        font-size: 13px;
        font-weight: 600;
        outline: none;
      }
      .search::placeholder { color: var(--c-text-3); font-weight: 600; }
      .search:focus { border-color: rgba(51,112,255,0.35); box-shadow: 0 0 0 3px rgba(51,112,255,0.10), var(--shadow-sm); }
      
      .van-dropdown-item { position: absolute; top: 44px; left: 0; right: 0; z-index: 30; display: none; }
      .van-dropdown-item.open { display: block; }
      .van-dropdown-item__content { width: 100%; background: rgba(255, 255, 255, 0.96); backdrop-filter: blur(28px); -webkit-backdrop-filter: blur(28px); border: 1px solid rgba(255, 255, 255, 0.95); border-radius: 16px; overflow: hidden; box-shadow: var(--shadow-lg); }
      
      .van-cell { display: flex; padding: 14px 16px; font-size: 14px; background: transparent; border: none; border-bottom: 1px solid rgba(15,23,42,0.04); width: 100%; text-align: left; align-items: center; justify-content: space-between; cursor: pointer; color: var(--c-text-1); font-weight: 600; transition: 0.2s; }
      .van-cell:last-child { border-bottom: none; }
      .van-cell:active { background: rgba(255, 255, 255, 0.6); }
      .van-cell--active { color: var(--c-primary); }
      .van-cell__value { display: flex; align-items: center; }

      .van-overlay { position: absolute; inset: 0; background: rgba(15,23,42,0.03); backdrop-filter: none; display: none; z-index: 25; }
      .van-overlay.open { display: block; }

      .list { margin-top: 12px; display: grid; gap: 12px; }

      .cell { background: var(--c-card); backdrop-filter: var(--glass-blur); -webkit-backdrop-filter: var(--glass-blur); border: 1px solid var(--c-border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 14px; position: relative; overflow: hidden; transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1); }
      .cell::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: transparent; transition: 0.3s; }
      .cell[data-level='high']::before { background: var(--c-risk-h); }
      .cell[data-level='mid']::before { background: var(--c-risk-m); }
      .cell[data-level='low']::before { background: var(--c-risk-l); }
      .cell[data-expanded='true'] { box-shadow: 0 0 0 2px var(--c-primary), var(--shadow-lg); transform: translateY(-1px); border-color: transparent; }
      
      .top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
      .l { display: flex; gap: 10px; align-items: flex-start; min-width: 0; }
      .r { display: flex; gap: 6px; align-items: center; flex: none; }
      .title { font-size: 16px; font-weight: 800; color: var(--c-text-1); line-height: 1.3; min-width: 0; }
      
      .risk-badge { height: 22px; padding: 0 8px; border-radius: 6px; font-size: 12px; font-weight: 700; display: inline-flex; align-items: center; background: rgba(255,255,255,0.9); box-shadow: var(--shadow-sm); }
      .risk-badge.high { color: #dc2626; border: 1px solid #fecaca; background: #fef2f2; }
      .risk-badge.mid { color: #d97706; border: 1px solid #fde68a; background: #fffbeb; }
      .risk-badge.low { color: #059669; border: 1px solid #bbf7d0; background: #f0fdf4; }
      
      .van-tag { display: inline-flex; align-items: center; padding: 0 6px; height: 20px; font-size: 11px; font-weight: 700; border-radius: 4px; }
      .van-tag--success { background: #f0fdf4; color: #059669; border: 1px solid #bbf7d0; }
      .van-tag--danger { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
      .res { margin-left: 4px; height: 22px; border-radius: 6px; padding: 0 8px; font-weight: 700; }

      .top-suggest { margin-top: 12px; }
      .suggest { display: flex; gap: 8px; align-items: baseline; }
      .suggest .v { font-size: 13px; color: var(--c-text-1); line-height: 1.5; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; font-weight: 500; }
      .hl-approve { display: inline-flex; align-items: center; height: 24px; padding: 0 8px; border-radius: 6px; border: 1px solid #bbf7d0; background: #f0fdf4; color: #059669; font-weight: 700; margin: 0; }
      .hl-reject { display: inline-flex; align-items: center; height: 24px; padding: 0 8px; border-radius: 6px; border: 1px solid #fde68a; background: #fffbeb; color: #d97706; font-weight: 700; margin: 0; }
      .hl-immediate { display: inline-flex; align-items: center; height: 24px; padding: 0 8px; border-radius: 6px; border: 1px solid #fecaca; background: #fef2f2; color: #dc2626; font-weight: 700; margin: 0; }

      .meta { margin-top: 10px; font-size: 13px; color: var(--c-text-2); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      .meta.compact { margin-top: 12px; }
      .amt { color: var(--c-text-1); font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
      .risk-count { color: #dc2626; font-weight: 600; background: #fef2f2; padding: 2px 6px; border-radius: 4px; border: 1px solid #fecaca; }
      
      .sub { margin-top: 10px; display: flex; align-items: center; justify-content: space-between; gap: 10px; border-top: 1px solid var(--c-border-solid); padding-top: 10px; }
      .sub.compact { border-top: none; padding-top: 0; }
      .time { font-size: 12px; color: var(--c-text-3); font-weight: 500; }
      
      .mini-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-items: stretch; }
      .mini { width: 100%; justify-content: center; border: 1px solid var(--c-border-solid); background: rgba(255,255,255,0.8); color: var(--c-text-2); height: 28px; padding: 0 12px; border-radius: 999px; font-size: 12px; font-weight: 700; cursor: pointer; outline: none; transition: 0.2s; box-shadow: var(--shadow-sm); }
      .mini:active { opacity: 0.8; }
      .mini.active { background: #eff6ff; border-color: #bfdbfe; color: var(--c-primary); }

      .toggles { margin-top: 12px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
      .van-button { position: relative; display: inline-flex; box-sizing: border-box; align-items: center; justify-content: center; text-align: center; border: 1px solid var(--c-border-solid); cursor: pointer; transition: all 0.2s; -webkit-appearance: none; background: rgba(255,255,255,0.8); color: var(--c-text-2); font-weight: 700; border-radius: 10px; backdrop-filter: var(--glass-blur); box-shadow: var(--shadow-sm); }
      .van-button:active { transform: scale(0.98); }
      .toggle-btn { width: 100%; height: 36px; font-size: 14px; padding: 0 16px; justify-content: center; }
      .toggle-btn.van-button--primary { background: #eff6ff; color: var(--c-primary); border-color: #bfdbfe; }
      
      .inline-actions { margin-top: 12px; display: flex; gap: 8px; }
      .act { height: 38px; font-weight: 800; font-size: 14px; flex: 1; border-radius: 10px; padding: 0; }
      .act[data-act="approve"] { background: var(--c-primary); color: #fff; border: 1px solid var(--c-primary); box-shadow: 0 4px 14px rgba(51,112,255,0.2); }
      .act[data-act="reject"] { background: rgba(255,255,255,0.9); color: #dc2626; border: 1px solid #fecaca; box-shadow: var(--shadow-sm); }

      .expand { margin-top: 12px; display: grid; gap: 10px; }
      .sec { padding: 12px; border-radius: 12px; background: rgba(255, 255, 255, 0.5); border: 1px solid rgba(255,255,255,0.8); box-shadow: inset 0 2px 8px rgba(255,255,255,0.6); }
      .sec-h { font-size: 13px; font-weight: 800; color: var(--c-text-1); margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
      .sec-h::before { content: ''; display: block; width: 4px; height: 12px; background: var(--c-primary); border-radius: 2px; }
      .sec-b { font-size: 13px; color: var(--c-text-2); line-height: 1.6; white-space: pre-wrap; }
      
      .risk-list { display: grid; gap: 10px; }
      .risk-row { display: flex; gap: 10px; align-items: flex-start; }
      .rr { flex: 1; min-width: 0; }
      .rk { flex: none; width: 24px; height: 24px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 800; background: #f1f5f9; border: 1px solid #e2e8f0; color: var(--c-text-3); }
      .rk[data-level='high'] { background: #fef2f2; color: #dc2626; border-color: #fecaca; }
      .rk[data-level='mid'] { background: #fffbeb; color: #d97706; border-color: #fde68a; }
      .rk[data-level='low'] { background: #f0fdf4; color: #059669; border-color: #bbf7d0; }
      .rt { font-size: 14px; color: var(--c-text-1); font-weight: 700; line-height: 1.4; margin-top: 2px; }
      .rd { margin-top: 4px; font-size: 13px; color: var(--c-text-2); line-height: 1.5; }

      .empty { padding: 48px 16px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--c-text-3); font-size: 15px; font-weight: 600; }
      
      .toast { position: absolute; left: 50%; transform: translateX(-50%); bottom: 80px; background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(12px); color: #fff; padding: 12px 20px; border-radius: 12px; font-size: 14px; z-index: 2000; opacity: 0; transition: opacity 0.3s, transform 0.3s; pointer-events: none; text-align: center; max-width: 80%; font-weight: 600; box-shadow: 0 10px 40px rgba(0,0,0,0.2); }
      .toast.show { opacity: 1; transform: translateX(-50%) translateY(-10px); }

      .checkbox { width: 24px; height: 24px; border-radius: 8px; border: 2px solid rgba(15,23,42,0.15); display: flex; align-items: center; justify-content: center; flex: none; transition: 0.2s; background: rgba(255,255,255,0.9); margin-right: 4px; }
      .checkbox.checked { background: var(--c-primary); border-color: var(--c-primary); box-shadow: 0 2px 8px rgba(51,112,255,0.25); }
      .checkbox.checked::after { content: ''; width: 10px; height: 5px; border-left: 2px solid #fff; border-bottom: 2px solid #fff; transform: rotate(-45deg) translateY(-1px) translateX(1px); }

      .card-wrap { display: flex; align-items: center; gap: 8px; }
      .card-wrap .checkbox-col { flex: none; display: flex; align-items: center; justify-content: center; width: 28px; }
      .card-wrap .cell { flex: 1; min-width: 0; }

      .bottom-bar, .all-bar {
        position: absolute;
        bottom: 0;
        left: 0;
        width: 100%;
        background: rgba(255, 255, 255, 0.7);
        backdrop-filter: blur(24px);
        -webkit-backdrop-filter: blur(24px);
        border-top: 1px solid rgba(255,255,255,0.9);
        padding: 16px 16px calc(16px + env(safe-area-inset-bottom));
        display: flex;
        gap: 12px;
        z-index: 30;
        transform: translateY(100%);
        transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
        box-shadow: 0 -10px 40px rgba(15, 23, 42, 0.05);
      }
      .bottom-bar.show, .all-bar:not(.hide) {
        transform: translateY(0);
      }
      .all-bar.hide { transform: translateY(100%); }
      
      .bottom-bar .van-button, .all-bar .van-button { flex: 1; height: 42px; border-radius: 12px; font-weight: 800; font-size: 14px; padding: 0; border: none; }
      .van-button--primary { background: var(--c-primary); color: #fff; box-shadow: 0 4px 14px rgba(51,112,255,0.2); }
      .van-button--danger { background: rgba(255,255,255,0.9); color: #dc2626; border: 1px solid rgba(239,68,68,0.35); box-shadow: var(--shadow-sm); }
      .van-button--plain.van-button--primary { background: rgba(255,255,255,0.9); color: var(--c-primary); border: 1px solid rgba(51,112,255,0.25); box-shadow: var(--shadow-sm); }

      .van-button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }
  `;

  return (
    <div className="pre-demo-host">
      <style>{css}</style>
      <div className="app-shell" id="app">
        <div className="fixed-header">
          <div className="top-hero">
            <div className="top-hero-inner">
              <div className="van-nav-bar">
                <div className="van-nav-bar__title">流程中心</div>
              </div>
            </div>
          </div>

          <div className="top-tabs" role="tablist" aria-label="待办与已办">
            <div className="tab-group">
              <button className={`tab ${tab === 'todo' ? 'active' : ''}`} onClick={() => setTabSafe('todo')} role="tab" aria-selected={tab === 'todo'}>
                待办 <span className="tab-count">{counts.tabCnt.todo}</span>
              </button>
              <button className={`tab ${tab === 'done' ? 'active' : ''}`} onClick={() => setTabSafe('done')} role="tab" aria-selected={tab === 'done'}>
                已办 <span className="tab-count">{counts.tabCnt.done}</span>
              </button>
            </div>
          </div>

          <div className="page-header">
            <div className="panel" aria-label="建议与风险筛选">
              <div className="row">
                <button className={`seg ${suggest === 'pass' ? 'active' : ''}`} data-level="high" onClick={() => setSuggestFilter('pass')}>
                  <div className="pair"><div className="label">AI 建议通过</div><div className="value">{counts.sugCnt.pass}</div></div>
                </button>
                <div className="divider"></div>
                <button className={`seg ${suggest === 'reject' ? 'active' : ''}`} data-level="mid" onClick={() => setSuggestFilter('reject')}>
                  <div className="pair"><div className="label">AI 建议驳回</div><div className="value">{counts.sugCnt.reject}</div></div>
                </button>
                <div className="divider"></div>
                <button className={`seg ${suggest === 'manual' ? 'active' : ''}`} data-level="low" onClick={() => setSuggestFilter('manual')}>
                  <div className="pair"><div className="label">人工判断</div><div className="value">{counts.sugCnt.manual}</div></div>
                </button>
              </div>
              <div className="divider-h"></div>
              <div className="row">
                <button className={`seg ${risk === 'high' ? 'active' : ''}`} data-level="high" onClick={() => toggleRiskFilter('high')}>
                  <div className="pair"><div className="label">高风险</div><div className="value">{counts.riskCnt.high}</div></div>
                </button>
                <div className="divider"></div>
                <button className={`seg ${risk === 'mid' ? 'active' : ''}`} data-level="mid" onClick={() => toggleRiskFilter('mid')}>
                  <div className="pair"><div className="label">中风险</div><div className="value">{counts.riskCnt.mid}</div></div>
                </button>
                <div className="divider"></div>
                <button className={`seg ${risk === 'low' ? 'active' : ''}`} data-level="low" onClick={() => toggleRiskFilter('low')}>
                  <div className="pair"><div className="label">低风险</div><div className="value">{counts.riskCnt.low}</div></div>
                </button>
              </div>
            </div>

            <div className="toolbar">
              <div className="toolbar-row">
                <div className="van-dropdown-menu__bar" onClick={() => setSortOpen((v) => !v)}>
                  <span className={`van-dropdown-menu__title ${sortOpen ? 'active' : ''}`}>{sortText(sort)}</span>
                </div>
                <input className="search" type="search" placeholder="搜索" autoComplete="off" value={q} onChange={(e) => setQ((e.target as any).value || '')} />
                <div className={`van-overlay ${sortOpen ? 'open' : ''}`} onClick={() => setSortOpen(false)} />
                <div className={`van-dropdown-item ${sortOpen ? 'open' : ''}`}>
                  <div className="van-dropdown-item__content">
                    {(['risk', 'time_desc', 'time_asc', 'amount_desc', 'amount_asc'] as SortKey[]).map((k) => (
                      <button
                        key={k}
                        className={`van-cell ${sort === k ? 'van-cell--active' : ''}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSort(k);
                          setSortOpen(false);
                        }}
                        data-sort={k}
                      >
                        <div className="van-cell__title"><span>{sortText(k)}</span></div>
                        <div className="van-cell__value" />
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              {tab === 'todo' && (
                <button className={`batch-trigger-btn ${batchMode ? 'active' : ''}`} onClick={onBatchToggle}>
                  {batchMode ? '取消选择' : '批量选择'}
                </button>
              )}
            </div>

            <div style={{ marginTop: 10 }}>
              <select
                value={eventId ?? ''}
                onChange={(e) => setEventId(Number((e.target as any).value))}
                style={{
                  width: '100%',
                  borderRadius: 12,
                  border: '1px solid rgba(15,23,42,0.10)',
                  background: 'rgba(255,255,255,0.7)',
                  padding: '10px 12px',
                  fontWeight: 700,
                  fontSize: 12,
                  outline: 'none',
                }}
              >
                {events.map((e) => (
                  <option key={e.id} value={e.id}>
                    #{e.id} · {toText(e.kind).trim()} · {toText(e.title).trim() || toText(e.url).trim()}
                  </option>
                ))}
              </select>
              {err ? (
                <div style={{ marginTop: 8, fontSize: 12, color: '#b91c1c', fontWeight: 700 }}>{err}</div>
              ) : null}
              {loading ? (
                <div style={{ marginTop: 8, fontSize: 12, color: '#475569', fontWeight: 700 }}>加载中…</div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="scroll-area">
          <div className="list">
            {visibleList.length === 0 ? (
              <div className="empty">暂无数据</div>
            ) : (
              visibleList.map((it) => {
                const isDone = it.status === 'done';
                const riskBadgeName = it.riskLevel === 'high' ? '高' : it.riskLevel === 'mid' ? '中' : '低';
                const sugTokens = tokenizeSuggestion(normalizeSuggestion(it.preApprovalSuggestion, it.riskLevel));
                const riskOn = expandedRisk.has(it.id);
                const aiOn = expandedAi.has(it.id);
                const isSelected = selected.has(it.id);
                const expanded = riskOn || aiOn;

                const metaParts: any[] = [it.applicantName, '·', it.applicantDept];
                if (it.amountCny != null) metaParts.push('·', it.amountCny != null ? fmtCny(it.amountCny) : '');
                if (it.riskItems.length > 0) metaParts.push('·', `风险 ${it.riskItems.length} 项`);

                return (
                  <div className="card-wrap" key={it.id}>
                    {!isDone && batchMode ? (
                      <div className="checkbox-col" onClick={() => onToggleSelect(it.id)} style={{ cursor: 'pointer', alignSelf: 'stretch' }}>
                        <div className={`checkbox ${isSelected ? 'checked' : ''}`} />
                      </div>
                    ) : null}

                    <div
                      className="cell"
                      data-level={it.riskLevel}
                      data-expanded={expanded ? 'true' : 'false'}
                      onClick={() => {
                        if (batchMode && !isDone) onToggleSelect(it.id);
                      }}
                      style={batchMode && !isDone ? ({ cursor: 'pointer' } as any) : undefined}
                    >
                      <div className="top">
                        <div className="l">
                          <div className="title">{it.title}</div>
                        </div>
                        <div className="r">
                          <div className={`risk-badge ${it.riskLevel}`}>{riskBadgeName}风险</div>
                          {isDone && it.decision ? (
                            <span className={`van-tag res ${it.decision === 'rejected' || it.decision === 'transferred' ? 'van-tag--danger' : 'van-tag--success'}`}>
                              {it.decision === 'approved' ? '通过' : '驳回'}
                            </span>
                          ) : null}
                        </div>
                      </div>

                      {!isDone ? (
                        <>
                          <div className="suggest top-suggest">
                            <span className="v">
                              {sugTokens.map((t, idx) => {
                                const tone = t.tone;
                                if (!tone) return <span key={idx}>{t.t}</span>;
                                return <span key={idx} className={`hl-${tone}`}>{t.t}</span>;
                              })}
                            </span>
                          </div>
                          <div className="meta">
                            {metaParts.map((p, idx) => {
                              if (p === '·') return <span key={idx}>·</span>;
                              if (typeof p === 'string' && p.startsWith('风险 ')) return <span key={idx} className="risk-count">{p}</span>;
                              if (typeof p === 'string' && p.startsWith('¥')) return <span key={idx} className="amt">{p}</span>;
                              return <span key={idx}>{p}</span>;
                            })}
                          </div>
                          <div className="sub">
                            <span className="time">提交于 {fmtTime(it.createdAtISO)}</span>
                          </div>
                          <div className="toggles">
                            <button className={`van-button toggle-btn ${aiOn ? 'van-button--primary' : 'van-button--default'}`} onClick={(e) => { e.stopPropagation(); toggleExpand('ai', it.id); }}>
                              AI 摘要
                            </button>
                            <button className={`van-button toggle-btn ${riskOn ? 'van-button--primary' : 'van-button--default'}`} onClick={(e) => { e.stopPropagation(); toggleExpand('risk', it.id); }}>
                              AI 风险提醒
                            </button>
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="meta compact">
                            {metaParts.map((p, idx) => {
                              if (p === '·') return <span key={idx}>·</span>;
                              if (typeof p === 'string' && p.startsWith('风险 ')) return <span key={idx} className="risk-count">{p}</span>;
                              if (typeof p === 'string' && p.startsWith('¥')) return <span key={idx} className="amt">{p}</span>;
                              return <span key={idx}>{p}</span>;
                            })}
                          </div>
                          <div className="sub compact">
                            <span className="time">完成于 {fmtTime(it.decidedAtISO || it.createdAtISO)}</span>
                            <div className="mini-actions">
                              <button className={`mini ${aiOn ? 'active' : ''}`} onClick={(e) => { e.stopPropagation(); toggleExpand('ai', it.id); }}>AI 摘要</button>
                              <button className={`mini ${riskOn ? 'active' : ''}`} onClick={(e) => { e.stopPropagation(); toggleExpand('risk', it.id); }}>AI 风险提醒</button>
                            </div>
                          </div>
                        </>
                      )}

                      {expanded ? (
                        <div className="expand">
                          {aiOn ? (
                            <div className="sec">
                              <div className="sec-h">AI 摘要</div>
                              <div className="sec-b" style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px', fontSize: 13 }}>
                                {it.aiSummary ? it.aiSummary.split('\n').map((line, idx) => {
                                  const parts = line.split(': ');
                                  const label = parts[0] || '';
                                  const value = parts.slice(1).join(': ') || '';
                                  return (
                                    <span key={`${it.id}-ai-${idx}`} style={{ display: 'contents' }}>
                                      <span style={{ color: 'var(--c-text-3)', whiteSpace: 'nowrap' }}>{label}:</span>
                                      <span style={{ color: 'var(--c-text-1)', fontWeight: 500 }}>{value || ''}</span>
                                    </span>
                                  );
                                }) : '暂无'}
                              </div>
                            </div>
                          ) : null}

                          {riskOn ? (
                            <div className="sec">
                              {it.riskSummary ? (
                                <div className="sec-b" style={{ marginBottom: 10, paddingBottom: 10, borderBottom: '1px dashed rgba(15, 23, 42, 0.08)' }}>
                                  <strong>总结：</strong>{it.riskSummary}
                                </div>
                              ) : null}
                              <div className="sec-h">风险项（{it.riskItems.length}）</div>
                              {it.riskItems.length === 0 ? (
                                <div className="sec-b">无风险项</div>
                              ) : (
                                <div className="risk-list">
                                  {it.riskItems.map((ri) => (
                                    <div className="risk-row" key={ri.id}>
                                      <span className="rk" data-level={ri.level}>{ri.level === 'high' ? '高' : ri.level === 'mid' ? '中' : '低'}</span>
                                      <div className="rr">
                                        <div className="rt">{ri.title}</div>
                                        <div className="rd">{ri.description}</div>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          ) : null}
                        </div>
                      ) : null}

                      {!isDone && !batchMode ? (
                        <div className="inline-actions">
                          <button className="van-button act" data-act="approve" onClick={(e) => { e.stopPropagation(); if (!confirmYesNo('确认同意该审批吗？')) return; decide(it.id, 'approved'); }}>
                            同意
                          </button>
                          <button className="van-button act" data-act="reject" onClick={(e) => { e.stopPropagation(); if (!confirmYesNo('确认驳回该审批吗？')) return; decide(it.id, 'rejected'); }}>
                            驳回
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className={`bottom-bar ${tab === 'todo' && batchMode ? 'show' : ''}`}>
          <button className="van-button van-button--primary" disabled={selected.size === 0} onClick={batchApprove}>
            批量同意({selected.size})
          </button>
          <button className="van-button van-button--danger" disabled={selected.size === 0} onClick={batchReject}>
            批量驳回({selected.size})
          </button>
          <button className="van-button van-button--plain van-button--primary" disabled={selected.size === 0} onClick={batchAI}>
            批量接受AI建议({selected.size})
          </button>
        </div>

        <div className={`all-bar ${tab === 'todo' && !batchMode && visibleList.length > 0 ? '' : 'hide'}`}>
          <button className="van-button van-button--primary" onClick={allApprove}>全部同意</button>
          <button className="van-button van-button--danger" onClick={allReject}>全部驳回</button>
          <button className="van-button van-button--plain van-button--primary" onClick={allAI}>全部接受AI建议</button>
        </div>
      </div>

      <div className={`toast ${toastMsg ? 'show' : ''}`}>{toastMsg}</div>
    </div>
  );
}
