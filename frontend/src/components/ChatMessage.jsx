import MetadataRow from './MetadataRow.jsx';

export default function ChatMessage({ message }) {
  if (message.role === 'user') {
    return (
      <div className="turn user">
        <div className="bubble" data-testid="user-message">
          {message.content}
        </div>
      </div>
    );
  }

  if (message.role === 'error') {
    return (
      <div className="turn assistant">
        <div className="bubble error" data-testid="error-message">
          <span className="error-badge">{message.status ? `HTTP ${message.status}` : 'Error'}</span>
          <span>{message.content}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="turn assistant">
      <div className="bubble assistant-bubble" data-testid="assistant-message">
        <div className="reply">{message.content}</div>
        {message.metadata ? <MetadataRow metadata={message.metadata} /> : null}
      </div>
    </div>
  );
}
