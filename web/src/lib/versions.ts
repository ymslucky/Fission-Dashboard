/**
 * 批 2 · 版本页纯函数：时间格式化 / 排序 / 别名指向 / 回滚闸门。
 *
 * 重构说明（允许的测试基础设施重构）：以下逻辑原内联在 Versions.tsx
 * （fmtTime、createdOf、useMemo 内排序、v.name === aliasTarget 判定），
 * 抽取为纯函数后 Versions.tsx 引用本模块，行为不变。
 */
import type { VersionInfo } from '../api/extend';

export interface AliasRef {
  name: string;
  target: string;
}

export const COLD_START_WARNING = '目标版本若无 warm Pod，回滚后首调将经历一次普通冷启动';

/** BE (versions.py) 返回 created；creationTimestamp 仅兜底 */
export function createdOf(v: VersionInfo): string | undefined {
  return v.created ?? v.creationTimestamp;
}

/** created 有值 / 缺失回退 creationTimestamp / 两者都缺 → '—'；非法时间串原样返回 */
export function fmtVersionTime(created?: string, creationTimestamp?: string): string {
  const ts = created ?? creationTimestamp;
  if (!ts) return '—';
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString('zh-CN', { hour12: false });
}

/** creationTimestamp 降序（新→旧），返回新数组不改动入参 */
export function sortVersions(list: readonly VersionInfo[]): VersionInfo[] {
  const copy = [...list];
  copy.sort((a, b) => (createdOf(b) ?? '').localeCompare(createdOf(a) ?? ''));
  return copy;
}

/** 别名指向高亮判定：aliases.find(a => a.target === name) */
export function currentAliasOf(aliases: readonly AliasRef[], name: string): AliasRef | undefined {
  return aliases.find((a) => a.target === name);
}

export type RollbackGateResult =
  | { ok: true; toVersion: string; coldStartWarning: string }
  | { ok: false; reason: string };

/**
 * 回滚闸门（前端预检；后端 versions.py 仍做权威校验）：
 * - 版本号不在列表 → 拒绝('版本不存在')
 * - 等于当前别名指向 → 拒绝('已指向该版本')
 * - 合法 → { ok, toVersion, coldStartWarning 必附 }
 */
export function rollbackGate(
  inputVersion: string,
  versions: readonly (VersionInfo | string)[],
  alias: AliasRef | null | undefined,
): RollbackGateResult {
  const names = versions.map((v) => (typeof v === 'string' ? v : v.name));
  if (!names.includes(inputVersion)) {
    return { ok: false, reason: '版本不存在' };
  }
  if (alias && alias.target === inputVersion) {
    return { ok: false, reason: '已指向该版本' };
  }
  return { ok: true, toVersion: inputVersion, coldStartWarning: COLD_START_WARNING };
}
