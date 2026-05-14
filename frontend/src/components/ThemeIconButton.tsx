import { useAppPreferences, type AppTheme } from "../i18n";

function ThemeGlyph({ theme }: { theme: AppTheme }) {
  if (theme === "dark") {
    return (
      <svg viewBox="0 0 24 24" width="17" height="17" stroke="currentColor" strokeWidth="2" fill="none" aria-hidden="true">
        <path d="M21 12.8A8.6 8.6 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z" />
      </svg>
    );
  }
  if (theme === "light") {
    return (
      <svg viewBox="0 0 24 24" width="17" height="17" stroke="currentColor" strokeWidth="2" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" stroke="currentColor" strokeWidth="2" fill="none" aria-hidden="true">
      <rect x="3" y="4" width="18" height="13" rx="2" />
      <path d="M8 21h8m-4-4v4" />
    </svg>
  );
}

type ThemeIconButtonProps = {
  className?: string;
  showLabel?: boolean;
};

export function ThemeIconButton({ className = "", showLabel = false }: ThemeIconButtonProps) {
  const { setTheme, t, theme } = useAppPreferences();
  const nextTheme: AppTheme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";

  return (
    <button
      type="button"
      className={`theme-icon-button ${showLabel ? "theme-icon-button--with-label" : ""} ${className}`.trim()}
      title={t(`theme.${theme}`)}
      aria-label={t(`theme.${theme}`)}
      onClick={() => setTheme(nextTheme)}
    >
      <ThemeGlyph theme={theme} />
      {showLabel ? <span>{t(`theme.${theme}`)}</span> : null}
    </button>
  );
}
