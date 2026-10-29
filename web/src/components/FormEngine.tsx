import { useState, type FormEvent, type ReactElement } from 'react';

export interface FormField {
  name: string;
  label: string;
  type?: 'text' | 'number' | 'select';
  options?: string[];
  required?: boolean;
  default?: string;
}

export interface FormEngineProps {
  fields: FormField[];
  onSubmit: (values: Record<string, string>) => void | Promise<void>;
  submitLabel?: string;
}

/** Minimal schema-driven form renderer; to be restyled later. */
export default function FormEngine({ fields, onSubmit, submitLabel = 'Submit' }: FormEngineProps): ReactElement {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.name, f.default ?? ''])),
  );
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setBusy(true);
    try {
      await onSubmit(values);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={(e) => { void handleSubmit(e); }} style={{ display: 'grid', gap: '0.75rem', maxWidth: '32rem' }}>
      {fields.map((f) => (
        <label key={f.name} style={{ display: 'grid', gap: '0.25rem' }}>
          <span>{f.label}{f.required ? ' *' : ''}</span>
          {f.type === 'select' ? (
            <select
              value={values[f.name] ?? ''}
              required={f.required}
              onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
            >
              <option value="" disabled>Select…</option>
              {(f.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <input
              type={f.type === 'number' ? 'number' : 'text'}
              value={values[f.name] ?? ''}
              required={f.required}
              onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
            />
          )}
        </label>
      ))}
      <button type="submit" disabled={busy}>{busy ? 'Saving…' : submitLabel}</button>
    </form>
  );
}
