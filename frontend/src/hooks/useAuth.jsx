import { createContext, useContext, useState, useEffect } from 'react';
import { api, getToken, getUsername } from '../services/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(getUsername());
  const [token, setToken] = useState(getToken());
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  // Synced state check
  const isAuthenticated = !!token;

  useEffect(() => {
    // Listen for auth expiration events dispatched by api client
    const handleAuthExpired = () => {
      setUser(null);
      setToken(null);
      setError('Your session has expired. Please log in again.');
    };

    window.addEventListener('auth-expired', handleAuthExpired);
    return () => window.removeEventListener('auth-expired', handleAuthExpired);
  }, []);

  const login = async (username, password) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.auth.login(username, password);
      setToken(data.access_token);
      setUser(username);
      return true;
    } catch (err) {
      setError(err.message || 'Login failed. Please check your credentials.');
      return false;
    } finally {
      setIsLoading(false);
    }
  };

  const register = async (username, password) => {
    setIsLoading(true);
    setError(null);
    try {
      await api.auth.register(username, password);
      // Automatically log in after registration
      return await login(username, password);
    } catch (err) {
      setError(err.message || 'Registration failed. Username may already exist.');
      return false;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = () => {
    api.auth.logout();
    setUser(null);
    setToken(null);
    setError(null);
  };

  const clearError = () => setError(null);

  const value = {
    user,
    token,
    isAuthenticated,
    isLoading,
    error,
    login,
    register,
    logout,
    clearError,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
