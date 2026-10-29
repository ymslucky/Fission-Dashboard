import { useEffect, type ReactElement, type ReactNode } from 'react';

export interface DrawerProps {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

export default function Drawer({ open, title, subtitle, onClose, children, footer }: DrawerProps): ReactElement | null {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="overlay" style={{ justifyContent: 'flex-end' }} onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="ins-h" style={{ marginBottom: 12 }}>
          <h2>{title}</h2>
          {subtitle ? <span className="muted mono" style={{ fontSize: 12 }}>{subtitle}</span> : null}
          <button type="button" className="btn sm" style={{ marginLeft: 'auto' }} onClick={onClose}>关闭</button>
        </div>
        {children}
        {footer ? <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>{footer}</div> : null}
      </aside>
    </div>
  );
}
