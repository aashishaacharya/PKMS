import { apiService } from './api';
import {
  AuthResponse,
  LoginCredentials,
  UserSetup,
  PasswordChange,
  RecoverySetup,
  RecoveryReset,
  RecoveryKeyResponse,
  User
} from '../types/auth';

class AuthService {
  // User setup (first-time password creation)
  async setupUser(userData: UserSetup): Promise<AuthResponse> {
    const response = await apiService.post<AuthResponse>('/auth/setup', userData);
    return response.data;
  }

  // User login
  async login(credentials: LoginCredentials): Promise<AuthResponse> {
    const response = await apiService.post<AuthResponse>('/auth/login', credentials);
    return response.data;
  }

  // User logout
  async logout(): Promise<{ message: string }> {
    const response = await apiService.post<{ message: string }>('/auth/logout');
    return response.data;
  }

  // Change password
  async changePassword(passwordData: PasswordChange): Promise<{ message: string }> {
    const response = await apiService.put<{ message: string }>('/auth/password', passwordData);
    return response.data;
  }

  // Setup recovery questions
  async setupRecovery(recoveryData: RecoverySetup): Promise<RecoveryKeyResponse> {
    const response = await apiService.post<RecoveryKeyResponse>('/auth/recovery/setup', recoveryData);
    return response.data;
  }

  // Reset password using recovery
  async resetPassword(resetData: RecoveryReset): Promise<{ message: string }> {
    const response = await apiService.post<{ message: string }>('/auth/recovery/reset', resetData);
    return response.data;
  }

  // Get user's security questions for recovery
  async getRecoveryQuestions(): Promise<{ questions: string[] }> {
    const response = await apiService.get<{ questions: string[] }>('/auth/recovery/questions');
    return response.data;
  }

  // Master-recovery endpoints have been removed; related methods deprecated.

  // Get current user info
  async getCurrentUser(): Promise<User> {
    const response = await apiService.get<User>('/auth/me');
    return response.data;
  }

  // Complete setup
  async completeSetup(): Promise<{ message: string }> {
    const response = await apiService.post<{ message: string }>('/auth/complete-setup');
    return response.data;
  }

  // Refresh token
  async refreshToken(): Promise<AuthResponse> {
    const response = await apiService.post<AuthResponse>('/auth/refresh');
    return response.data;
  }

  // Local storage management
  saveAuthData(authResponse: AuthResponse): void {
    localStorage.setItem('pkms_token', authResponse.access_token);
    localStorage.setItem('pkms_user', JSON.stringify({
      id: authResponse.user_id,
      username: authResponse.username,
      is_first_login: authResponse.is_first_login
    }));
  }

  clearAuthData(): void {
    localStorage.removeItem('pkms_token');
    localStorage.removeItem('pkms_user');
  }

  getStoredToken(): string | null {
    return localStorage.getItem('pkms_token');
  }

  getStoredUser(): User | null {
    const userStr = localStorage.getItem('pkms_user');
    if (userStr) {
      try {
        return JSON.parse(userStr);
      } catch {
        return null;
      }
    }
    return null;
  }

  isAuthenticated(): boolean {
    return !!this.getStoredToken();
  }

  // Login Password Hint Methods (separate from diary encryption hints)
  async setLoginPasswordHint(hint: string): Promise<{ message: string }> {
    const response = await apiService.put<{ message: string }>('/auth/login-password-hint', { hint });
    return response.data;
  }

  async getLoginPasswordHint(username: string): Promise<string> {
    const response = await apiService.post<{ hint: string }>('/auth/login-password-hint', { username });
    return response.data.hint;
  }
}

const authService = new AuthService();
export default authService; 