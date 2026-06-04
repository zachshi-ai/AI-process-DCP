export type DesktopBackendStatus = {
  desired?: boolean
  state?: string
  port?: number
  pid?: number | null
  apiBase?: string
  userDataDir?: string
  lastError?: string | null
}

export type DesktopBackendLog = {
  ts: number
  source: string
  line: string
}

export type DesktopMetrics = {
  ts: number
  port: number
  state: string
  cpu: number | null
  memoryMB: number | null
}

type DesktopApi = {
  getBackendStatus: () => Promise<DesktopBackendStatus>
  startBackend: () => Promise<any>
  stopBackend: () => Promise<any>
  restartBackend: () => Promise<any>
  getBackendLogs: (args?: { limit?: number; keyword?: string }) => Promise<DesktopBackendLog[]>
  getBackendMetrics: () => Promise<DesktopMetrics>
  onBackendLog: (cb: (log: DesktopBackendLog) => void) => () => void
  onBackendStatus: (cb: (st: DesktopBackendStatus) => void) => () => void
  secureAvailable: () => Promise<{ keytar: boolean; touchId: boolean }>
  secureSet: (args: { service?: string; account?: string; secret: string }) => Promise<{ ok: boolean; error?: string }>
  secureGet: (args: { reason?: string; service?: string; account?: string }) => Promise<{ ok: boolean; secret?: string; error?: string }>
  openWebView: (args: { url: string; userAgent?: string; partition?: string }) => Promise<any>
  listCookies: (args?: { partition?: string }) => Promise<any[]>
  clearCookies: (args?: { partition?: string }) => Promise<any>
  pickHistoryDbs?: () => Promise<{ canceled?: boolean; filePaths?: string[] } | null>
  setApiBase: (apiBase: string) => void
}

declare global {
  interface Window {
    __AI_DCP__?: DesktopApi
    __AI_DCP_API_BASE?: string
    __AI_DCP_DESKTOP?: boolean
  }
}

export function isDesktopApp() {
  return !!window.__AI_DCP_DESKTOP && !!window.__AI_DCP__
}

export function getDesktopApi(): DesktopApi | null {
  return window.__AI_DCP__ || null
}
