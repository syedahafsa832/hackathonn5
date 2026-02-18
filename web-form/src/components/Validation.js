/**
 * Validation utility functions for form validation
 */

export const validateField = (fieldName, value) => {
  switch (fieldName) {
    case 'email':
      return isValidEmail(value);
    case 'phone':
      return isValidPhone(value);
    case 'name':
      return isValidName(value);
    case 'subject':
      return isValidSubject(value);
    case 'message':
      return isValidMessage(value);
    default:
      return true;
  }
};

export const isValidEmail = (email) => {
  if (!email) return false;
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return emailRegex.test(email);
};

export const isValidPhone = (phone) => {
  if (!phone) return false;
  // Simple phone validation - can be enhanced based on requirements
  const phoneRegex = /^[\+]?[1-9][\d]{0,15}$/;
  return phoneRegex.test(phone.replace(/[\s\-\(\)]/g, ''));
};

export const isValidName = (name) => {
  if (!name || typeof name !== 'string') return false;
  return name.trim().length >= 2 && name.trim().length <= 50;
};

export const isValidSubject = (subject) => {
  if (!subject || typeof subject !== 'string') return false;
  return subject.trim().length >= 3 && subject.trim().length <= 100;
};

export const isValidMessage = (message) => {
  if (!message || typeof message !== 'string') return false;
  return message.trim().length >= 10 && message.trim().length <= 5000;
};

/**
 * Validate entire form data
 */
export const validateFormData = (formData) => {
  const errors = {};

  // Validate required fields
  if (!formData.name || !isValidName(formData.name)) {
    errors.name = 'Valid name is required (2-50 characters)';
  }

  if (!formData.email || !isValidEmail(formData.email)) {
    errors.email = 'Valid email is required';
  }

  if (!formData.subject || !isValidSubject(formData.subject)) {
    errors.subject = 'Valid subject is required (3-100 characters)';
  }

  if (!formData.message || !isValidMessage(formData.message)) {
    errors.message = 'Valid message is required (10-5000 characters)';
  }

  // Validate optional fields if provided
  if (formData.company && formData.company.length > 255) {
    errors.company = 'Company name must be less than 255 characters';
  }

  return {
    isValid: Object.keys(errors).length === 0,
    errors
  };
};

/**
 * Sanitize form data to prevent XSS
 */
export const sanitizeFormData = (formData) => {
  const sanitized = {};

  for (const [key, value] of Object.entries(formData)) {
    if (typeof value === 'string') {
      sanitized[key] = value.trim();
    } else {
      sanitized[key] = value;
    }
  }

  return sanitized;
};