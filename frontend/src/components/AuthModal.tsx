import { LoginPage } from "../pages/LoginPage";

type AuthModalProps = {
  open: boolean;
  onClose: () => void;
  onLoggedIn: () => void;
};

export function AuthModal({ open, onClose, onLoggedIn }: AuthModalProps) {
  if (!open) return null;

  return (
    <div className="auth-modal" role="dialog" aria-modal="true" aria-label="Login">
      <button type="button" className="auth-modal__backdrop" onClick={onClose} aria-label="Close login" />
      <div className="auth-modal__panel">
        <button type="button" className="auth-modal__close" onClick={onClose} aria-label="Close login">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" strokeWidth="2" fill="none">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
        <LoginPage variant="modal" onLoggedIn={onLoggedIn} />
      </div>
    </div>
  );
}
