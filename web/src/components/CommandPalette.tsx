import { useEffect, useMemo, useState, type ReactElement } from 'react';

export interface Command {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

export interface CommandPaletteProps {
  commands: Command[];
  placeholder?: string;
}

/** Ctrl+K global command palette: view jumps + function search. */
export default function CommandPalette({ commands, placeholder = '跳转视图或搜索函数…' }: CommandPaletteProps): ReactElement | null {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((o) => !o);
        setQuery('');
        setActive(0);
      }
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands.slice(0, 12);
    return commands.filter((c) => c.label.toLowerCase().includes(q) || (c.hint ?? '').toLowerCase().includes(q)).slice(0, 12);
  }, [commands, query]);

  if (!open) return null;

  const execute = (i: number): void => {
    const c = filtered[i];
    if (!c) return;
    c.run();
    setOpen(false);
    setQuery('');
  };

  return (
    <div
      className="overlay center"
      style={{ alignItems: 'flex-start', paddingTop: '12vh' }}
      role="dialog"
      aria-label="命令面板"
      onClick={() => setOpen(false)}
    >
      <div className="modal" style={{ minWidth: 460, padding: 8 }} onClick={(e) => e.stopPropagation()}>
        <input
          autoFocus
          value={query}
          placeholder={placeholder}
          onChange={(e) => { setQuery(e.target.value); setActive(0); }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, filtered.length - 1)); }
            if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            if (e.key === 'Enter') execute(active);
          }}
          style={{ width: '100%', padding: '8px 10px', border: '1px solid var(--hairline-strong)', borderRadius: 2, background: '#fff', fontSize: 13 }}
        />
        <ul style={{ listStyle: 'none', margin: '6px 0 0', padding: 0 }}>
          {filtered.length === 0 ? <li className="empty">无匹配命令</li> : null}
          {filtered.map((c, i) => (
            <li key={c.id}>
              <button
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => execute(i)}
                style={{
                  width: '100%', textAlign: 'left', padding: '7px 10px', cursor: 'pointer',
                  background: i === active ? 'var(--obs-faint)' : 'none',
                  border: 'none', borderLeft: i === active ? '3px solid var(--obs)' : '3px solid transparent',
                  display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 13,
                }}
              >
                {c.label}
                {c.hint ? <span className="muted mono" style={{ fontSize: 10, marginLeft: 'auto' }}>{c.hint}</span> : null}
              </button>
            </li>
          ))}
        </ul>
        <div className="cap" style={{ padding: '4px 10px 6px' }}>↑↓ 选择 · Enter 执行 · Esc 关闭</div>
      </div>
    </div>
  );
}
