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
        initialize: (config: {
          client_id: string;
          callback: (response: { credential: string }) => void;
          ux_mode?: "popup" | "redirect";
          use_fedcm_for_button?: boolean;
          error_callback?: (error: { type?: string; message?: string }) => void;
        }) => void;
        renderButton: (parent: HTMLElement, options: { type?: string; theme?: string; size?: string; text?: string; width?: number }) => void;
        disableAutoSelect: () => void;
      };
      oauth2?: {
        initTokenClient: (config: {
          client_id: string;
          scope: string;
          callback: (response: { access_token?: string; error?: string; error_description?: string }) => void;
          error_callback?: (error: { type?: string; message?: string }) => void;
        }) => { requestAccessToken: (override?: { prompt?: string }) => void };
      };
    };
  };
}
