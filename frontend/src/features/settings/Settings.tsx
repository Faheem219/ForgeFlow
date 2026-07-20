import { Panel } from '../../components/ui';
import Credentials from './Credentials';

export default function Settings(): JSX.Element {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-slate-100">Settings</h1>
      <Credentials />
      <Panel title="Account">
        <p className="text-sm text-slate-400">Account preferences arrive with phase 52.</p>
      </Panel>
    </div>
  );
}
