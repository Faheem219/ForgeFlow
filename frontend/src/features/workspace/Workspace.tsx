import { useParams } from 'react-router-dom';

import { Panel } from '../../components/ui';

export default function Workspace(): JSX.Element {
  const { id } = useParams();

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-slate-100">Workspace</h1>
      <Panel title={`Project ${id ?? ''}`}>
        <p className="text-sm text-slate-400">
          The stage navigator, chat, Monaco IDE, and live preview land in phase 09 and later.
        </p>
      </Panel>
    </div>
  );
}
