export default function Composer({ value, onChange, onSend, busy }) {
  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  }

  function handleChange(event) {
    const node = event.target;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, 160)}px`;
    onChange(node.value);
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        onSend();
      }}
    >
      <textarea
        className="chat-input"
        data-testid="chat-input"
        aria-label="Message"
        rows={1}
        placeholder="Ask something... (Enter to send, Shift+Enter for a new line)"
        value={value}
        disabled={busy}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
      />
      <button
        className="send-button"
        data-testid="send-button"
        type="submit"
        disabled={busy || !value.trim()}
      >
        {busy ? 'Sending...' : 'Send'}
      </button>
    </form>
  );
}
