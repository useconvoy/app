import { EpisodeReplay } from "@/components/configurations/EpisodeReplay";
import type { Episode } from "@/lib/platform/client";
import { executionDiagnosis } from "@/lib/platform/diagnosis";

export function EpisodeSummary({ episode }: { episode: Episode }) {
  const diagnosis = executionDiagnosis(episode.summary.failure);
  return <>
    <p>Benchmark task at the final step: <strong>{episode.summary.final_success === true ? "Succeeded" : episode.summary.final_success === false ? "Did not succeed" : "Not reported"}</strong></p>
    {diagnosis && <div className="console-diagnosis">
      <h4>Where execution stopped</h4>
      <p><strong>{diagnosis.component}</strong>: {diagnosis.stage}.</p>
      <p>{diagnosis.category}.</p>
      {diagnosis.authorizationNotice && <p className="console-note">{diagnosis.authorizationNotice}</p>}
    </div>}
    <p className="console-note">Release digest <code>{episode.release_digest}</code></p>
    <EpisodeReplay key={episode.id} episodeId={episode.id} />
    <details><summary>Technical details</summary><pre className="console-code">{JSON.stringify(episode.summary, null, 2)}</pre></details>
    <p className="console-note">This is the coordinator-reported summary. Mission completion and benchmark task success are separate outcomes.</p>
  </>;
}
