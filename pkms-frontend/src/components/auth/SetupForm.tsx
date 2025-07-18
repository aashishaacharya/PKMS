import { useState } from 'react';
import {
  Paper,
  TextInput,
  PasswordInput,
  Button,
  Title,
  Text,
  Anchor,
  Stack,
  Alert,
  LoadingOverlay,
  Progress,
  List,
  Group,
  Divider,
  Select,
  Card,
  Badge,
  Textarea
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconAlertCircle, IconUserPlus, IconCheck, IconShield, IconLock } from '@tabler/icons-react';
import { useAuthStore } from '../../stores/authStore';
import { UserSetup } from '../../types/auth';

interface SetupFormProps {
  onSwitchToLogin: () => void;
}

// Predefined security questions
const SECURITY_QUESTIONS = [
  "What was the name of your first pet?",
  "What is your mother's maiden name?",
  "What was the name of your first school?",
  "What city were you born in?",
  "What was your childhood nickname?",
  "What is the name of your favorite teacher?",
  "What was the make of your first car?",
  "What is your favorite book?",
  "What was the name of the street you grew up on?",
  "What is your favorite movie?",
  "What was your first job?",
  "What is the name of your best friend from childhood?",
  "What was your favorite food as a child?",
  "What high school did you attend?",
  "What was the name of your first boss?",
  "What is your favorite color?",
  "What was the name of your first boyfriend/girlfriend?",
  "What city did you meet your spouse in?",
  "What is your favorite vacation destination?",
  "What was your grandmother's first name?"
];

// Password strength checker
const getPasswordStrength = (password: string) => {
  let strength = 0;
  const checks = {
    length: password.length >= 8,
    uppercase: /[A-Z]/.test(password),
    lowercase: /[a-z]/.test(password),
    number: /\d/.test(password),
    special: /[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]/.test(password)
  };

  Object.values(checks).forEach(check => {
    if (check) strength += 20;
  });

  return { strength, checks };
};

export function SetupForm({ onSwitchToLogin }: SetupFormProps) {
  const [isLoading, setIsLoading] = useState(false);
  const { setupUser, error, clearError } = useAuthStore();

  const form = useForm<{
    username: string;
    password: string;
    email: string;
    login_password_hint?: string;
    recovery_questions: string[];
    recovery_answers: string[];
    diary_password?: string;
    diary_password_hint?: string;
  }>({
    initialValues: {
      username: '',
      password: '',
      email: '',
      login_password_hint: '',
      recovery_questions: ['', ''],
      recovery_answers: ['', ''],
      diary_password: '',
      diary_password_hint: '',
    },
    validate: {
      username: (value) => {
        if (value.length < 3) return 'Username must be at least 3 characters long';
        if (!/^[a-zA-Z0-9_-]+$/.test(value)) return 'Username can only contain letters, numbers, hyphens, and underscores';
        return null;
      },
      password: (value) => {
        const { checks } = getPasswordStrength(value);
        if (!checks.length) return 'Password must be at least 8 characters long';
        if (!checks.uppercase) return 'Password must contain at least one uppercase letter';
        if (!checks.lowercase) return 'Password must contain at least one lowercase letter';
        if (!checks.number) return 'Password must contain at least one number';
        if (!checks.special) return 'Password must contain at least one special character';
        return null;
      },
      email: (value: string | undefined) => {
        const emailVal = value ?? '';
        if (!emailVal.trim()) {
          return 'Email is required';
        }
        if (!/^\S+@\S+\.\S+$/.test(emailVal)) {
          return 'Please enter a valid email address';
        }
        return null;
      },
      recovery_questions: (value) => {
        if (!value[0] || !value[1]) return 'Please select both security questions';
        if (value[0] === value[1]) return 'Please select different security questions';
        return null;
      },
      recovery_answers: (value) => {
        if (!value[0]?.trim() || !value[1]?.trim()) return 'Please provide answers to both security questions';
        if (value[0].length < 3 || value[1].length < 3) return 'Answers must be at least 3 characters long';
        return null;
      },
      diary_password: (value) => {
        if (value && value.length > 0 && value.length < 8) {
          return 'Diary password must be at least 8 characters long';
        }
        return null;
      },
      login_password_hint: (value) => {
        if (value && value.length > 255) {
          return 'Hint must be 255 characters or less';
        }
        return null;
      },
    },
  });

  const { strength, checks } = getPasswordStrength(form.values.password);

  const handleSubmit = async (values: typeof form.values) => {
    clearError();
    setIsLoading(true);
    
    try {
      const setupData: UserSetup = {
        username: values.username,
        password: values.password,
        email: values.email,
        recovery_questions: values.recovery_questions,
        recovery_answers: values.recovery_answers,
        diary_password: values.diary_password || undefined,
        diary_password_hint: values.diary_password_hint || undefined,
        login_password_hint: values.login_password_hint || undefined,
      };

      const success = await setupUser(setupData);
      
      if (success) {
        // Success! User is now logged in and everything is set up
        // No need for additional modals or flows
      }
    } finally {
      setIsLoading(false);
    }
  };

  const getStrengthColor = () => {
    if (strength < 40) return 'red';
    if (strength < 80) return 'yellow';
    return 'green';
  };

  const getAvailableQuestions = (selectedQuestions: string[]) => {
    return SECURITY_QUESTIONS.filter(q => !selectedQuestions.includes(q));
  };

  return (
    <Paper withBorder shadow="md" p={30} mt={30} radius="md" style={{ position: 'relative', maxWidth: 600, margin: '30px auto' }}>
      <LoadingOverlay visible={isLoading} />
      
      <Title order={2} ta="center" mb="md">
        Set up your PKMS
      </Title>
      
      <Text c="dimmed" size="sm" ta="center" mb="xl">
        Complete registration with security questions and optional diary encryption
      </Text>

      {error && (
        <Alert icon={<IconAlertCircle size="1rem" />} color="red" mb="md">
          {error}
        </Alert>
      )}

      <form onSubmit={form.onSubmit(handleSubmit)}>
        <Stack gap="lg">
          {/* Basic Account Information */}
          <Card withBorder p="md">
            <Text size="sm" fw={500} mb="md" c="blue">Account Information</Text>
            <Stack gap="md">
              <TextInput
                label="Username"
                placeholder="Choose a username"
                description="3+ characters, letters, numbers, hyphens, and underscores only"
                required
                {...form.getInputProps('username')}
              />

              <TextInput
                label="Email"
                placeholder="your.email@example.com"
                description="For account recovery purposes"
                required
                {...form.getInputProps('email')}
              />

              <div>
                <PasswordInput
                  label="Login Password"
                  placeholder="Create a strong password"
                  description="This will be your main login password for accessing PKMS"
                  required
                  {...form.getInputProps('password')}
                />
                
                <TextInput
                  label="Login Password Hint (Optional)"
                  placeholder="Enter a hint to help you remember your login password"
                  description="This hint can appear on the login screen if you forget your password"
                  {...form.getInputProps('login_password_hint')}
                />
                
                {form.values.password && (
                  <div style={{ marginTop: '8px' }}>
                    <Group justify="space-between" mb="xs">
                      <Text size="sm" fw={500}>
                        Password Strength
                      </Text>
                      <Text size="sm" c={getStrengthColor()}>
                        {strength < 40 ? 'Weak' : strength < 80 ? 'Good' : 'Strong'}
                      </Text>
                    </Group>
                    
                    <Progress 
                      value={strength} 
                      color={getStrengthColor()} 
                      size="sm" 
                      mb="xs"
                    />
                    
                    <List size="xs" spacing="xs">
                      <List.Item 
                        icon={checks.length ? <IconCheck size="0.8rem" color="green" /> : null}
                        c={checks.length ? 'green' : 'dimmed'}
                      >
                        At least 8 characters
                      </List.Item>
                      <List.Item 
                        icon={checks.uppercase ? <IconCheck size="0.8rem" color="green" /> : null}
                        c={checks.uppercase ? 'green' : 'dimmed'}
                      >
                        One uppercase letter
                      </List.Item>
                      <List.Item 
                        icon={checks.lowercase ? <IconCheck size="0.8rem" color="green" /> : null}
                        c={checks.lowercase ? 'green' : 'dimmed'}
                      >
                        One lowercase letter
                      </List.Item>
                      <List.Item 
                        icon={checks.number ? <IconCheck size="0.8rem" color="green" /> : null}
                        c={checks.number ? 'green' : 'dimmed'}
                      >
                        One number
                      </List.Item>
                      <List.Item 
                        icon={checks.special ? <IconCheck size="0.8rem" color="green" /> : null}
                        c={checks.special ? 'green' : 'dimmed'}
                      >
                        One special character (!@#$%^&*)
                      </List.Item>
                    </List>
                  </div>
                )}
              </div>
            </Stack>
          </Card>

          {/* Security Questions - Required */}
          <Card withBorder p="md">
            <Group gap="xs" mb="md">
              <IconShield size={16} color="blue" />
              <Text size="sm" fw={500} c="blue">Security Questions</Text>
              <Badge color="red" variant="dot">Required</Badge>
            </Group>
            
            <Alert color="blue" variant="light" mb="md">
              <Text size="sm">
                Security questions help you recover your account if you forget your password. 
                Choose questions only you would know the answers to.
              </Text>
            </Alert>

            <Stack gap="md">
              {/* Question 1 */}
              <div>
                <Select
                  label="Security Question 1"
                  placeholder="Select your first security question..."
                  data={SECURITY_QUESTIONS}
                  searchable
                  required
                  value={form.values.recovery_questions[0]}
                  onChange={(value) => 
                    form.setFieldValue('recovery_questions', [value || '', form.values.recovery_questions[1]])
                  }
                  error={form.errors.recovery_questions}
                />
                
                <Textarea
                  label="Your answer"
                  placeholder="Enter your answer (case-sensitive)"
                  mt="xs"
                  minRows={2}
                  required
                  value={form.values.recovery_answers[0]}
                  onChange={(event) =>
                    form.setFieldValue('recovery_answers', [event.currentTarget.value, form.values.recovery_answers[1]])
                  }
                  error={form.errors.recovery_answers}
                />
              </div>

              {/* Question 2 */}
              <div>
                <Select
                  label="Security Question 2"
                  placeholder="Select your second security question..."
                  data={getAvailableQuestions([form.values.recovery_questions[0]])}
                  searchable
                  required
                  value={form.values.recovery_questions[1]}
                  onChange={(value) => 
                    form.setFieldValue('recovery_questions', [form.values.recovery_questions[0], value || ''])
                  }
                />
                
                <Textarea
                  label="Your answer"
                  placeholder="Enter your answer (case-sensitive)"
                  mt="xs"
                  minRows={2}
                  required
                  value={form.values.recovery_answers[1]}
                  onChange={(event) =>
                    form.setFieldValue('recovery_answers', [form.values.recovery_answers[0], event.currentTarget.value])
                  }
                />
              </div>
            </Stack>
          </Card>

          {/* Diary Encryption - Optional */}
          <Card withBorder p="md">
            <Group gap="xs" mb="md">
              <IconLock size={16} color="purple" />
              <Text size="sm" fw={500} c="purple">Diary Encryption</Text>
              <Badge color="gray" variant="dot">Optional</Badge>
            </Group>
            
            <Alert color="purple" variant="light" mb="md">
              <Text size="sm">
                Set up a separate password for encrypting your diary entries. This adds an extra layer of privacy.
                If you skip this now, you can set it up later in the diary section.
              </Text>
            </Alert>

            <Stack gap="md">
              <PasswordInput
                label="Diary Password (Optional)"
                placeholder="Enter a password for diary encryption"
                description="This will be separate from your login password"
                {...form.getInputProps('diary_password')}
              />
              
              <TextInput
                label="Diary Password Hint (Optional)"
                placeholder="Enter a hint to help you remember your diary password"
                description="This hint will help you unlock your diary if forgotten"
                {...form.getInputProps('diary_password_hint')}
              />
            </Stack>
          </Card>

          <Alert color="orange" variant="light">
            <Text size="sm" fw={500} mb={4}>Important Security Notes:</Text>
            <List size="sm" spacing={2}>
              <List.Item>Answers are case-sensitive - remember exactly how you type them</List.Item>
              <List.Item>Choose questions only you would know the answers to</List.Item>
              <List.Item>Avoid answers that could be found on social media</List.Item>
              <List.Item>You'll need to answer ALL questions correctly to recover your account</List.Item>
            </List>
          </Alert>

          <Button
            type="submit"
            size="lg"
            leftSection={<IconUserPlus size={16} />}
            loading={isLoading}
          >
            Create Account & Complete Setup
          </Button>

          <Text ta="center" size="sm">
            Already have an account?{' '}
            <Anchor component="button" type="button" onClick={onSwitchToLogin}>
              Sign in here
            </Anchor>
          </Text>
        </Stack>
      </form>
    </Paper>
  );
} 