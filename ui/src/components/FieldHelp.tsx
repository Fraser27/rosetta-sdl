/**
 * FieldHelp — a small "(?)" info icon that reveals a tooltip on hover or keyboard
 * focus. CSS-only (no positioning library); accessible via tabindex + aria-label.
 * Use next to a form <label> to explain a field.
 */
export default function FieldHelp({ text }: { text: string }) {
  return (
    <span className="field-help" tabIndex={0} role="note" aria-label={text}>
      <span className="field-help-icon" aria-hidden="true">?</span>
      <span className="field-help-tip" role="tooltip">{text}</span>
    </span>
  )
}
