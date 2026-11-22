import React, { useId, useLayoutEffect, useRef } from 'react';

export function Modal({ title, onClose, children, footer }: {
  title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useLayoutEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog ref={ref} className="modal-mask" aria-labelledby={titleId} onClick={onClose}
      onCancel={event => { event.preventDefault(); onClose(); }}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 id={titleId}>{title}</h3>
        {children}
        {footer && <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 16 }}>{footer}</div>}
      </div>
    </dialog>
  );
}
