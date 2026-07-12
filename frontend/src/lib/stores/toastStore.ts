import { create } from 'zustand';

export type ToastVariant = 'info' | 'success' | 'error' | 'warning';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  action?: ToastAction;
}

interface ToastState {
  toasts: Toast[];
  push: (toast: Toast) => void;
  dismiss: (id: string) => void;
}

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (toast) => set((state) => ({ toasts: [...state.toasts, toast] })),
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}));

let counter = 0;

export interface ToastOptions {
  title: string;
  description?: string;
  variant?: ToastVariant;
  action?: ToastAction;
  /** Auto-dismiss delay in ms; 0 disables auto-dismiss. */
  durationMs?: number;
}

/** Imperatively raise a toast. Returns the toast id. */
export function toast(options: ToastOptions): string {
  const id = `t-${Date.now()}-${counter++}`;
  const { title, description, variant = 'info', action, durationMs = 5000 } = options;
  useToastStore.getState().push({ id, title, description, variant, action });
  if (durationMs > 0) {
    setTimeout(() => useToastStore.getState().dismiss(id), durationMs);
  }
  return id;
}
