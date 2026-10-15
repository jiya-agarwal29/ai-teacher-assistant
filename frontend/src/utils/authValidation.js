import { z } from 'zod';

// Mirrors backend/main.py's RegisterRequest field validators exactly
// (USERNAME_PATTERN, PASSWORD_SPECIAL_CHAR_PATTERN) -- keep both in sync.
export const PASSWORD_RULES = [
  { key: 'length', label: 'At least 8 characters', test: (v) => v.length >= 8 },
  { key: 'uppercase', label: 'One uppercase letter', test: (v) => /[A-Z]/.test(v) },
  { key: 'lowercase', label: 'One lowercase letter', test: (v) => /[a-z]/.test(v) },
  { key: 'number', label: 'One number', test: (v) => /[0-9]/.test(v) },
  { key: 'special', label: 'One special character', test: (v) => /[^A-Za-z0-9]/.test(v) },
];

const usernameSchema = z
  .string()
  .min(3, 'Username must be at least 3 characters.')
  .max(30, 'Username must be at most 30 characters.')
  .regex(/^[A-Za-z0-9_]+$/, 'Username can only contain letters, numbers, and underscores.');

const strongPasswordSchema = z
  .string()
  .refine((v) => PASSWORD_RULES.every((rule) => rule.test(v)), {
    message: 'Password does not meet all the requirements below.',
  });

export const registerSchema = z
  .object({
    username: usernameSchema,
    password: strongPasswordSchema,
    confirmPassword: z.string().min(1, 'Please confirm your password.'),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: 'Passwords do not match.',
    path: ['confirmPassword'],
  });

// Login intentionally only checks "required" -- no strength rules, so an
// existing account with an old, simple password can still sign in.
export const loginSchema = z.object({
  username: z.string().min(1, 'Username is required.'),
  password: z.string().min(1, 'Password is required.'),
});

/** {field: firstErrorMessage} from a zod safeParse result, or {} if it passed. */
export function fieldErrorsFrom(result) {
  if (result.success) return {};
  const errors = {};
  for (const issue of result.error.issues) {
    const field = issue.path[0];
    if (field && !(field in errors)) errors[field] = issue.message;
  }
  return errors;
}
