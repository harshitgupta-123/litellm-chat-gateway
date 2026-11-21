import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ChatMessage from './components/ChatMessage.jsx';
import Composer from './components/Composer.jsx';
import SidePanel from './components/SidePanel.jsx';
import { API_BASE_URL, getHealth, newSessionId, postChat } from './api.js';

export default function App() {
  // A new session starts on every page load.
  const [sessionId, setSessionId] = useState(() => newSessionId());
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [simulateFailure, setSimulateFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState('');
  const transcriptRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((data) => !cancelled && setHealth(data))
      .catch((error) => !cancelled && setHealthError(error.message));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const node = transcriptRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, busy]);

  const send = useCallback(async () => {
    const message = draft.trim();
    if (!message || busy) return;

    setDraft('');
    setBusy(true);
    setMessages((current) => [...current, { role: 'user', content: message }]);

    try {
      const data = await postChat({
        sessionId,
        message,
        systemPrompt,
        simulateFailure: simulateFailure.trim(),
      });
      setMessages((current) => [
        ...current,
        { role: 'assistant', content: data.reply, metadata: data.metadata },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        { role: 'error', content: error.message, status: error.status },
      ]);
    } finally {
      setBusy(false);
    }
  }, [busy, draft, sessionId, simulateFailure, systemPrompt]);

  const startNewSession = useCallback(() => {
    setSessionId(newSessionId());
    setMessages([]);
    setDraft('');
  }, []);

  const totals = useMemo(() => {
    const assistant = messages.filter((m) => m.role === 'assistant' && m.metadata);
    return {
      turns: messages.filter((m) => m.role === 'user' || m.role === 'assistant').length,
      cached: assistant.filter((m) => m.metadata.resolution_source === 'LLM_CACHE').length,
      live: assistant.filter((m) => m.metadata.resolution_source === 'LIVE_INFERENCE').length,
    };
  }, [messages]);

  return (
    <div className="layout">
      <main className="chat">
        <header className="chat-header">
          <div>
            <h1>LiteLLM Chat Gateway</h1>
            <p className="sub">
              Multi-turn chat with key-pool fallback, response caching and per-request token
              tracking.
            </p>
          </div>
          <div className="counters">
            <span className="counter live">{totals.live} live</span>
            <span className="counter cache">{totals.cached} cached</span>
          </div>
        </header>

        <div className="transcript" ref={transcriptRef}>
          {messages.length === 0 ? (
            <div className="empty">
              <h3>Start the conversation</h3>
              <p>
                Ask the same question twice in two sessions to see <b>LLM_CACHE</b>, or turn on the
                failover demo to watch the key pool fail over.
              </p>
            </div>
          ) : (
            messages.map((message, index) => (
              <ChatMessage key={`${message.role}-${index}`} message={message} />
            ))
          )}
          {busy ? (
            <div className="turn assistant">
              <div className="bubble assistant-bubble typing">
                <span /> <span /> <span />
              </div>
            </div>
          ) : null}
        </div>

        <Composer value={draft} onChange={setDraft} onSend={send} busy={busy} />
      </main>

      <SidePanel
        sessionId={sessionId}
        onNewSession={startNewSession}
        systemPrompt={systemPrompt}
        onSystemPromptChange={setSystemPrompt}
        simulateFailure={simulateFailure}
        onSimulateFailureChange={setSimulateFailure}
        health={health}
        healthError={healthError}
        apiBaseUrl={API_BASE_URL}
        turnCount={totals.turns}
      />
    </div>
  );
}
