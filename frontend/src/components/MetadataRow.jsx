export default function MetadataRow({ metadata }) {
  const source = metadata.resolution_source;
  const cached = source === 'LLM_CACHE';

  return (
    <div className="meta-row">
      <span
        className={`meta-chip source ${cached ? 'cache' : 'live'}`}
        data-testid="meta-source"
        title="Where this reply came from"
      >
        {source}
      </span>
      <span className="meta-chip" data-testid="meta-model" title="LiteLLM model">
        {metadata.model}
      </span>
      <span className="meta-chip" data-testid="meta-latency" title="Server measured latency">
        {metadata.latency_ms} ms
      </span>
      <span className="meta-chip" title="Prompt tokens for this request">
        in&nbsp;
        <b data-testid="meta-input-tokens">{metadata.input_tokens}</b>
      </span>
      <span className="meta-chip" title="Completion tokens for this reply">
        out&nbsp;
        <b data-testid="meta-output-tokens">{metadata.output_tokens}</b>
      </span>
      {metadata.key_id ? (
        <span className="meta-chip key" data-testid="meta-key-id" title="Key that answered">
          {metadata.key_id}
        </span>
      ) : null}
      {metadata.fallback_attempts > 0 ? (
        <span className="meta-chip warn" data-testid="meta-fallback-attempts" title="Keys that failed first">
          {metadata.fallback_attempts} fallback{metadata.fallback_attempts > 1 ? 's' : ''}
        </span>
      ) : null}
    </div>
  );
}
