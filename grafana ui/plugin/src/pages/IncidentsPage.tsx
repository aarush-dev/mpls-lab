import React, { useEffect, useState } from 'react';
import { useParams, useHistory } from 'react-router-dom';
import { PluginPage } from '@grafana/runtime';

import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { IncidentTable } from '../components/IncidentTable';
import { IncidentDetail } from '../components/IncidentDetail';
import { incidentsPath, incidentDetailPath } from '../constants';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { Incident, Prediction } from '../data/types';

export function IncidentsPage() {
  const { refreshTick, range, filters } = useAppState();
  const { id } = useParams<{ id?: string }>();
  const history = useHistory();
  const dataClient = useDataClient();

  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // Detail selection is the URL: /incidents/:id is deep-linkable. Row click pushes the id; close
  // replaces back to the list (so Back doesn't re-open the detail).
  const selected = id ? incidents?.find((inc) => inc.id === id) ?? null : null;
  const notFound = !!id && incidents !== null && selected === null;

  useEffect(() => {
    let cancelled = false;
    setError(false);

    const withRange = { ...filters, timeRange: { fromMs: range.fromMs, toMs: range.toMs } };
    Promise.all([dataClient.getIncidents(withRange), dataClient.getPredictions(withRange)])
      .then(([incidentsResult, predictionsResult]) => {
        if (cancelled) {
          return;
        }
        setIncidents(incidentsResult);
        setPredictions(predictionsResult);
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [dataClient, refreshTick, range.fromMs, range.toMs, filters, attempt]);

  return (
    <PluginPage>
      <h1>Incidents & Predictions</h1>

      {error ? (
        <ErrorState onRetry={() => setAttempt((a) => a + 1)} />
      ) : incidents === null ? (
        <EmptyState message="Loading…" />
      ) : incidents.length === 0 && predictions.length === 0 ? (
        <EmptyState message="No incidents in the current window." />
      ) : (
        <>
          <IncidentTable
            incidents={incidents}
            predictions={predictions}
            onSelect={(inc) => history.push(incidentDetailPath(inc.id))}
            selectedId={selected?.id}
          />
          {selected && <IncidentDetail incident={selected} onClose={() => history.replace(incidentsPath)} />}
          {notFound && <EmptyState message={`Incident ${id} is not in the current window.`} />}
        </>
      )}
    </PluginPage>
  );
}
