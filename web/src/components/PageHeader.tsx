import type { ReactElement, ReactNode } from 'react';

export interface PageHeaderProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}

export default function PageHeader({ title, subtitle, actions }: PageHeaderProps): ReactElement {
  return (
    <header className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '1rem' }}>
      <div>
        <h2>{title}</h2>
        {subtitle ? <p style={{ color: 'var(--muted)' }}>{subtitle}</p> : null}
      </div>
      {actions ? <div style={{ display: 'flex', gap: '0.5rem' }}>{actions}</div> : null}
    </header>
  );
}
