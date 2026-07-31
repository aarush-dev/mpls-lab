import React, { useEffect, useState } from 'react';
import { PluginPage } from '@grafana/runtime';

import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { IncidentTable } from '../components/IncidentTable';
import { IncidentDetail } from '../components/IncidentDetail';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { Incident, Prediction } from '../data/types';

export function IncidentsPage() {
  const { cursor, filters } = useAppState();
  const dataClient = useDataClient();

  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [selected, setSelected] = useState<Incident | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(false);

    Promise.all([dataClient.getIncidents(filters), dataClient.getPredictions(filters)])
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
  }, [dataClient, cursor, filters, attempt]);

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
            onSelect={setSelected}
            selectedId={selected?.id}
          />
          {selected && <IncidentDetail incident={selected} onClose={() => setSelected(null)} />}
        </>
      )}
    </PluginPage>
  );
}
