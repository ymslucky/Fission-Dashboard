import { yaml } from '@codemirror/lang-yaml';
import { oneDark } from '@codemirror/theme-one-dark';
import CodeMirror from '@uiw/react-codemirror';
import type { ReactElement } from 'react';

export interface YamlEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  height?: string;
}

export default function YamlEditor({ value, onChange, readOnly = false, height = '400px' }: YamlEditorProps): ReactElement {
  return (
    <CodeMirror
      value={value}
      height={height}
      extensions={[yaml()]}
      theme={oneDark}
      editable={!readOnly}
      readOnly={readOnly}
      onChange={(v) => onChange?.(v)}
    />
  );
}
