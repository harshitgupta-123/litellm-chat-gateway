export default function SidePanel({
  sessionId,
  onNewSession,
  systemPrompt,
  onSystemPromptChange,
  simulateFailure,
  onSimulateFailureChange,
  health,
  healthError,
  apiBaseUrl,
  turnCount,
}) {
  const simulationOn = Boolean(simulateFailure);

  return (
    <aside className="panel">
      <section className="panel-block">
        <h2>Session</h2>
        <code className="session-id" title="Sent as session_id on every request">
          {sessionId}
        </code>
        <p className="hint">{turnCount} message{turnCount === 1 ? '' : 's'} stored in SQLite</p>
        <button className="ghost" type="button" onClick={onNewSession}>
          Start a new session
        </button>
      </section>

      <section className="panel-block">
        <h2>System prompt</h2>
        <textarea
          className="panel-input"
          data-testid="system-prompt-input"
          rows={4}
          placeholder="Optional. Overrides the default system prompt for this session."
          value={systemPrompt}
          onChange={(event) => onSystemPromptChange(event.target.value)}
        />
        <p className="hint">Sent with the next message, then remembered for the session.</p>
      </section>

      <section className="panel-block">
        <h2>Failover demo</h2>
        <p className="hint">
          Sends an <code>X-Simulate-Failure</code> header so the key pool can be exercised
          without burning real quota.
        </p>
        <div className="chip-row">
          {['', 'key_1=429', 'key_1=500', 'key_1=429,key_2=500', '*=429'].map((option) => (
            <button
              key={option || 'off'}
              type="button"
              className={`chip ${simulateFailure === option ? 'active' : ''}`}
              onClick={() => onSimulateFailureChange(option)}
            >
              {option || 'off'}
            </button>
          ))}
        </div>
        <input
          className="panel-input"
          data-testid="simulate-failure-input"
          placeholder="key_1=429,key_2=500"
          value={simulateFailure}
          onChange={(event) => onSimulateFailureChange(event.target.value)}
        />
        {simulationOn ? <p className="warn-text">Simulation active - cache is bypassed.</p> : null}
      </section>

      <section className="panel-block">
        <h2>Backend</h2>
        <code className="session-id">{apiBaseUrl}</code>
        {health ? (
          <ul className="facts" data-testid="health-facts">
            <li>
              <span>status</span>
              <b className="ok">{health.status}</b>
            </li>
            <li>
              <span>keys configured</span>
              <b>{health.keys_configured}</b>
            </li>
            <li>
              <span>cache</span>
              <b>{health.cache_enabled ? health.cache_backend || 'on' : 'off'}</b>
            </li>
            {health.models ? (
              <li>
                <span>models</span>
                <b>{health.models.join(', ')}</b>
              </li>
            ) : null}
          </ul>
        ) : (
          <p className="warn-text">{healthError || 'Checking /health ...'}</p>
        )}
      </section>
    </aside>
  );
}
