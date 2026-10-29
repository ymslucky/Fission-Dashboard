import { createContext, useCallback, useContext, useRef, useState, type ReactElement } from 'react';

export interface Toast {
  id: number;
  kind: 'info' | 'error';
  source: string;
  message: string;
}

interface ToastContextValue {
  show: (source: string, message: string, kind?: Toast['kind']) => void;
  showError: (source: string, err: unknown) => void;
}

const ToastContext = createContext<ToastContextValue>({ show: () => undefined, showError: () => undefined });

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}

function extractMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === 'string') return err;
  return JSON.stringify(err);
}

/** All write-operation errors flow here — RBAC rejections must not be silent. */
export function ToastProvider({ children }: { children: ReactElement | ReactElement[] }): ReactElement {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const show = useCallback((source: string, message: string, kind: Toast['kind'] = 'info') => {
    const id = nextId.current++;
    setToasts((old) => [...old.slice(-4), { id, kind, source, message }]);
    window.setTimeout(() => {
      setToasts((old) => old.filter((t) => t.id !== id));
    }, kind === 'error' ? 8000 : 4000);
  }, []);

  const showError = useCallback((source: string, err: unknown) => {
    show(source, extractMessage(err), 'error');
  }, [show]);

  return (
    <ToastContext.Provider value={{ show, showError }}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={'toast' + (t.kind === 'error' ? ' error' : '')}>
            <span className="t-kind">{t.kind === 'error' ? '▲ ' : ''}{t.source}</span>
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
