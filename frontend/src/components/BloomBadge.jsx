import useBloomLevels from '../hooks/useBloomLevels';
import { getBloomColor } from '../utils/bloomColors';

/**
 * A coloured pill showing a Bloom's taxonomy level's label (e.g. "Apply"),
 * with the level's description as its tooltip. The label/description come
 * from useBloomLevels (GET /bloom/levels), never hard-coded here -- only
 * the colour is a frontend-only choice (utils/bloomColors.js). Renders the
 * raw key if the level list hasn't loaded yet or `levelKey` is unknown,
 * so a badge never disappears while the one network call is in flight.
 */
export default function BloomBadge({ levelKey, className = '' }) {
  const { byKey } = useBloomLevels();
  const level = byKey[levelKey];
  const colors = getBloomColor(levelKey);

  return (
    <span
      title={level?.description || ''}
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full border text-[9px] font-bold uppercase tracking-wider whitespace-nowrap ${colors.pill} ${className}`}
    >
      {level?.label || levelKey}
    </span>
  );
}
