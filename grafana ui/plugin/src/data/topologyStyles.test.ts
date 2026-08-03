import { styleForRole, defaultRoleStyle, roleStyles, stateColors, colorForState, neutralColor } from './topologyStyles';

describe('styleForRole', () => {
  it('returns the registered style for a known role', () => {
    expect(styleForRole('p')).toEqual(roleStyles['p']);
    expect(styleForRole('p')).toEqual({ shape: 'diamond', color: '#5794f2', size: 42 });
  });

  it('falls back to defaultRoleStyle for an unknown role (new-node guarantee)', () => {
    expect(styleForRole('some-brand-new-role')).toBe(defaultRoleStyle);
  });
});

describe('colorForState', () => {
  it('returns stateColors.red for "red"', () => {
    expect(colorForState('red')).toBe(stateColors.red);
  });

  it('returns stateColors.amber for "amber"', () => {
    expect(colorForState('amber')).toBe(stateColors.amber);
  });

  it('returns stateColors.green for "green"', () => {
    expect(colorForState('green')).toBe(stateColors.green);
  });

  it('returns neutralColor when state is undefined', () => {
    expect(colorForState(undefined)).toBe(neutralColor);
    expect(colorForState()).toBe(neutralColor);
  });
});
