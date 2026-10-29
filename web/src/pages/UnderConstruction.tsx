import type { ReactElement } from 'react';
import { Link } from 'react-router-dom';

export default function UnderConstruction({ title, zh }: { title: string; zh: string }): ReactElement {
  return (
    <main className="page page-pad">
      <div className="pane-h">{title}<span>建设中</span></div>
      <div className="empty" style={{ padding: '64px 20px' }}>
        <div style={{ fontSize: 28, fontFamily: 'var(--font-display)', letterSpacing: 2, color: 'var(--gray-teal)' }}>{title.toUpperCase()}</div>
        <p style={{ marginTop: 12 }}>{zh}</p>
        <p style={{ marginTop: 4 }}>此观测面属于下一阶段（P1）规划，路由与导航已先行保留。</p>
        <p style={{ marginTop: 16 }}>
          <Link to="/overview" style={{ color: 'var(--obs)' }}>← 返回总览</Link>
        </p>
      </div>
    </main>
  );
}
