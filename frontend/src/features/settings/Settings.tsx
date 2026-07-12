import { Panel } from '../../components/ui';

export default function Settings(): JSX.Element {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-slate-100">Settings</h1>
      <Panel title="Account">
        <p className="text-sm text-slate-400">
          Credential (BYO tokens) and preference settings arrive with phases 34 and 52.
        </p>
      </Panel>
    </div>
  );
}
