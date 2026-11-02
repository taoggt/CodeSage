const KEY = "codesage.recentRoots";
const MAX = 6;

export function loadRecentRoots(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

export function pushRecentRoot(path: string): string[] {
  const next = [path, ...loadRecentRoots().filter((item) => !samePath(item, path))].slice(0, MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* 隐私模式写入失败：忽略，不影响主流程 */
  }
  return next;
}

export function removeRecentRoot(path: string): string[] {
  const next = loadRecentRoots().filter((item) => !samePath(item, path));
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* 同上 */
  }
  return next;
}

/** Windows 路径大小写/分隔符不敏感：比较前统一成小写正斜杠形式 */
export function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

export function samePath(a: string, b: string): boolean {
  return normalizePath(a) === normalizePath(b);
}

export function displayPath(path: string): string {
  return path.replace(/\\/g, "/");
}

export function folderName(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}
