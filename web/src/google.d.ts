interface ImportMetaEnv {
  readonly VITE_GOOGLE_CLIENT_ID?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Google Identity Services, loaded from https://accounts.google.com/gsi/client in index.html.
interface Window {
  google?: {
    accounts: {
      id: {
        initialize: (config: { client_id: string; callback: (response: { credential: string }) => void }) => void;
        renderButton: (parent: HTMLElement, options: { type?: string; theme?: string; size?: string; text?: string; width?: number }) => void;
        disableAutoSelect: () => void;
      };
    };
  };
}
