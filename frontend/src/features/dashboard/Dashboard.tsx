import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Badge, Button, EmptyState, Input, Modal, Panel, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import { STAGE_LABELS } from '../workspace/stageMeta';
import { createProject, listProjects } from './api';

export default function Dashboard(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState('');

  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: listProjects });

  const create = useMutation({
    mutationFn: (projectName: string) => createProject(projectName),
    onSuccess: (project) => {
      setModalOpen(false);
      setName('');
      void queryClient.invalidateQueries({ queryKey: ['projects'] });
      navigate(`/projects/${project.id}`);
    },
    onError: (err) => {
      const message = err instanceof ApiError ? err.message : 'Could not create project';
      toast({ title: 'Create failed', description: message, variant: 'error' });
    },
  });

  function onCreate(event: React.FormEvent): void {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed) {
      create.mutate(trimmed);
    }
  }

  const projects = projectsQuery.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Projects</h1>
          <p className="text-sm text-slate-400">Take an idea from design to a deployed URL.</p>
        </div>
        <Button onClick={() => setModalOpen(true)}>New project</Button>
      </div>

      <Panel title="Your projects">
        {projectsQuery.isLoading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : projects.length === 0 ? (
          <EmptyState
            title="No projects yet"
            description="Create your first project to open a workspace."
            action={<Button onClick={() => setModalOpen(true)}>New project</Button>}
          />
        ) : (
          <ul className="divide-y divide-slate-800" data-testid="project-list">
            {projects.map((project) => (
              <li key={project.id}>
                <button
                  type="button"
                  data-testid={`project-${project.id}`}
                  onClick={() => navigate(`/projects/${project.id}`)}
                  className="flex w-full items-center justify-between px-1 py-3 text-left hover:bg-slate-800/40"
                >
                  <span className="text-sm font-medium text-slate-100">{project.name}</span>
                  <Badge tone="brand">{STAGE_LABELS[project.current_stage]}</Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title="New project"
        footer={
          <>
            <Button variant="ghost" onClick={() => setModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={onCreate} loading={create.isPending} disabled={!name.trim()}>
              Create
            </Button>
          </>
        }
      >
        <form onSubmit={onCreate}>
          <Input
            label="Project name"
            value={name}
            autoFocus
            placeholder="My web app"
            onChange={(e) => setName(e.target.value)}
          />
        </form>
      </Modal>
    </div>
  );
}
