"use client";
import { createContext, useContext } from "react";
import useSWR, { SWRConfig } from "swr";
import { requestJson, RequestError } from "./lib/api";

const AuthContext = createContext({ authenticated: false });
function AuthProvider({ children }: { children: React.ReactNode }) {
  const { data } = useSWR("/api/auth/me", requestJson<{ authenticated: boolean }>, { revalidateOnFocus: false, revalidateIfStale: false, dedupingInterval: 60_000 });
  return <AuthContext.Provider value={{ authenticated: data?.authenticated || false }}>{children}</AuthContext.Provider>;
}
export const useAuth = () => useContext(AuthContext);
export default function Providers({ children }: { children: React.ReactNode }) {
  return <SWRConfig value={{ fetcher: requestJson, dedupingInterval: 30_000, keepPreviousData: true, shouldRetryOnError: (error: Error) => !(error instanceof RequestError && error.status < 500), errorRetryCount: 0 }}><AuthProvider>{children}</AuthProvider></SWRConfig>;
}
