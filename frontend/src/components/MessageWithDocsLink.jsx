import { Link } from 'react-router-dom';
import { PENDING_DOCUMENTS_MESSAGE } from '../services/api';

/**
 * Renders a backend "message" string as plain text, except for the one
 * exact message main.py sends when the user has documents but none are
 * "ready" yet -- that one also gets a link to the Documents page, so the
 * user has somewhere to go check on them instead of just a dead end.
 */
export default function MessageWithDocsLink({ message }) {
  if (!message) return null;

  if (message !== PENDING_DOCUMENTS_MESSAGE) {
    return <>{message}</>;
  }

  return (
    <>
      {message}{' '}
      <Link to="/documents" className="font-bold text-violet-600 dark:text-violet-400 hover:underline">
        Go to Documents
      </Link>
    </>
  );
}
