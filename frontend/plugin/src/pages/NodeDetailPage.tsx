import React from 'react';
import { useParams } from 'react-router-dom';
import { PluginPage } from '@grafana/runtime';

export function NodeDetailPage() {
  const { id } = useParams<{ id: string }>();

  return (
    <PluginPage>
      <h1>Node Detail{id ? `: ${id}` : ''}</h1>
      <p>Coming soon.</p>
    </PluginPage>
  );
}
