import { apiService } from './api';
import { coreDownloadService, DownloadProgress } from './shared/coreDownloadService';

// Types for todos
export interface Project {
  id: number;
  name: string;
  description?: string;
  color: string;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
  todo_count: number;
  completed_count: number;
}

export interface ProjectCreate {
  name: string;
  description?: string;
  color?: string;
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  color?: string;
  is_archived?: boolean;
}

export interface Todo {
  id: number;
  title: string;
  description?: string;
  project_id?: number;
  project_name?: string;
  due_date?: string;
  priority: number;
  status: string;
  completed_at?: string;
  is_recurring: boolean;
  recurrence_pattern?: string;
  created_at: string;
  updated_at: string;
  tags: string[];
  days_until_due?: number;
  is_archived: boolean;
}

export interface TodoCreate {
  title: string;
  description?: string;
  project_id?: number;
  due_date?: string;
  priority?: number;
  tags?: string[];
  is_recurring?: boolean;
  recurrence_pattern?: string;
  is_archived?: boolean;
}

export interface TodoUpdate {
  title?: string;
  description?: string;
  project_id?: number;
  due_date?: string;
  priority?: number;
  status?: string;
  tags?: string[];
  is_recurring?: boolean;
  recurrence_pattern?: string;
  is_archived?: boolean;
}

export interface TodoSummary {
  id: number;
  title: string;
  project_name?: string;
  due_date?: string;
  priority: number;
  status: string;
  created_at: string;
  tags: string[];
  days_until_due?: number;
  is_archived: boolean;
}

export interface TodoStats {
  total: number;
  pending: number;
  in_progress: number;
  completed: number;
  cancelled: number;
  overdue: number;
  due_today: number;
  due_this_week: number;
}

export interface TodoListParams {
  status?: string;
  priority?: number;
  project_id?: number;
  due_date?: string;
  overdue?: boolean;
  tag?: string;
  search?: string;
  limit?: number;
  offset?: number;
  is_archived?: boolean;
}

class TodosService {
  private baseUrl = '/todos';

  // Project methods
  async createProject(projectData: ProjectCreate): Promise<Project> {
    const response = await apiService.post<Project>(`${this.baseUrl}/projects`, projectData);
    return response.data;
  }

  async getProjects(archived: boolean = false): Promise<Project[]> {
    const url = `${this.baseUrl}/projects?archived=${archived}`;
    const response = await apiService.get<Project[]>(url);
    return response.data;
  }

  async getProject(projectId: number): Promise<Project> {
    const response = await apiService.get<Project>(`${this.baseUrl}/projects/${projectId}`);
    return response.data;
  }

  async updateProject(projectId: number, projectData: ProjectUpdate): Promise<Project> {
    const response = await apiService.put<Project>(`${this.baseUrl}/projects/${projectId}`, projectData);
    return response.data;
  }

  async deleteProject(projectId: number): Promise<void> {
    await apiService.delete(`${this.baseUrl}/projects/${projectId}`);
  }

  // Todo methods
  async createTodo(todoData: TodoCreate): Promise<Todo> {
    const response = await apiService.post<Todo>(`${this.baseUrl}/`, todoData);
    return response.data;
  }

  async getTodos(params: TodoListParams = {}): Promise<TodoSummary[]> {
    const queryParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        queryParams.append(key, value.toString());
      }
    });
    const url = `${this.baseUrl}/${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;
    const response = await apiService.get<TodoSummary[]>(url);
    return response.data;
  }

  async getTodo(todoId: number): Promise<Todo> {
    const response = await apiService.get<Todo>(`${this.baseUrl}/${todoId}`);
    return response.data;
  }

  async updateTodo(todoId: number, todoData: TodoUpdate): Promise<Todo> {
    const response = await apiService.put<Todo>(`${this.baseUrl}/${todoId}`, todoData);
    return response.data;
  }

  async completeTodo(todoId: number): Promise<Todo> {
    const response = await apiService.post<Todo>(`${this.baseUrl}/${todoId}/complete`);
    return response.data;
  }

  async deleteTodo(todoId: number): Promise<void> {
    await apiService.delete(`${this.baseUrl}/${todoId}`);
  }

  async archiveTodo(todoId: number, archive: boolean = true): Promise<Todo> {
    const response = await apiService.patch<Todo>(`${this.baseUrl}/${todoId}/archive?archive=${archive}`);
    return response.data;
  }

  /* ---------------------------------------------------------------------- */
  /*                               DOWNLOADS                                */
  /* ---------------------------------------------------------------------- */
  getExportUrl(todoId: number, format: 'pdf' | 'markdown' | 'txt' = 'pdf'): string {
    return `${this.baseUrl}/${todoId}/export?format=${format}`;
  }

  async downloadTodoExport(
    todoId: number,
    format: 'pdf' | 'markdown' | 'txt' = 'pdf',
    onProgress?: (p: DownloadProgress) => void,
  ): Promise<Blob> {
    return coreDownloadService.downloadFile(this.getExportUrl(todoId, format), {
      fileId: `${todoId}-${format}`,
      onProgress,
    });
  }

  // Statistics
  async getTodoStats(): Promise<TodoStats> {
    const response = await apiService.get<TodoStats>(`${this.baseUrl}/stats/overview`);
    return response.data;
  }

  // Utility methods
  getPriorityLabel(priority: number): string {
    const labels = {
      1: 'Low',
      2: 'Medium',
      3: 'High'
    };
    return labels[priority as keyof typeof labels] || 'Unknown';
  }

  getStatusLabel(status: string): string {
    const labels = {
      pending: 'Pending',
      in_progress: 'In Progress',
      completed: 'Completed',
      cancelled: 'Cancelled'
    };
    return labels[status as keyof typeof labels] || status;
  }

  getDaysUntilDue(dueDate: string): number | null {
    if (!dueDate) return null;
    
    const due = new Date(dueDate);
    const now = new Date();
    const diffTime = due.getTime() - now.getTime();
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    
    return diffDays;
  }

  isOverdue(dueDate: string): boolean {
    if (!dueDate) return false;
    const due = new Date(dueDate);
    const now = new Date();
    return due < now;
  }

  getPriorityColor(priority: number): string {
    const colors = {
      1: '#4CAF50', // Green for low
      2: '#FF9800', // Orange for medium
      3: '#F44336'  // Red for high
    };
    return colors[priority as keyof typeof colors] || '#757575';
  }

  getStatusColor(status: string): string {
    const colors = {
      pending: '#757575',
      in_progress: '#2196F3',
      completed: '#4CAF50',
      cancelled: '#F44336'
    };
    return colors[status as keyof typeof colors] || '#757575';
  }

  formatRecurrencePattern(pattern: string): string {
    if (!pattern) return '';
    
    // Simple formatting for common patterns
    const patterns = {
      daily: 'Every day',
      weekly: 'Every week',
      monthly: 'Every month',
      yearly: 'Every year'
    };
    
    return patterns[pattern as keyof typeof patterns] || pattern;
  }
}

export const todosService = new TodosService(); 