"use client";

import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
  children?: ReactNode;
  onCancel: () => void;
  onConfirm: () => void;
};

export default function ConfirmDialog({
  open, title, description, confirmLabel, cancelLabel = "Cancel", tone = "primary",
  children, onCancel, onConfirm,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  return <dialog
    ref={dialogRef}
    className="confirm-dialog"
    aria-labelledby={titleId}
    aria-describedby={description ? descriptionId : undefined}
    onCancel={event => { event.preventDefault(); onCancel(); }}
    onClick={event => { if (event.target === event.currentTarget) onCancel(); }}
  >
    <div className="confirm-dialog-content">
      <h2 id={titleId}>{title}</h2>
      {description && <p id={descriptionId} className="confirm-dialog-description">{description}</p>}
      {children && <div className="confirm-dialog-details">{children}</div>}
      <div className="confirm-dialog-actions">
        <button className="btn" type="button" autoFocus onClick={onCancel}>{cancelLabel}</button>
        <button className={`btn ${tone}`} type="button" onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </div>
  </dialog>;
}
