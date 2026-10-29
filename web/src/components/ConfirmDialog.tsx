import { useEffect, useState, type ReactElement } from 'react';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  /** the exact string the user must type to enable the confirm button */
  confirmName?: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Destructive-action gate: type the resource name to confirm. */
export default function ConfirmDialog({ open, title, message, confirmName, confirmLabel = '确认删除', onConfirm, onCancel }: ConfirmDialogProps): ReactElement | null {
  const [typed, setTyped] = useState('');

  useEffect(() => {
    if (open) setTyped('');
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;
  const ready = !confirmName || typed === confirmName;

  return (
    <div className="overlay center" role="alertdialog" aria-label={title} onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <p className="muted" style={{ marginBottom: 12 }}>{message}</p>
        {confirmName ? (
          <div className="field">
            <span>输入名称 <b className="mono">{confirmName}</b> 以确认</span>
            <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={confirmName} />
          </div>
        ) : null}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onCancel}>取消</button>
          <button type="button" className="btn danger pri" disabled={!ready} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
