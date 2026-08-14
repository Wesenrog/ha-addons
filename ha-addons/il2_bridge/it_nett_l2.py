#!/usr/bin/env python3
"""
Rekonstruer strommen i L2 i et trefase IT-nett (3 ledere, ingen N)
naar maaleren gir I_L1, I_L3, alle tre linjespenninger, samt P og Q.

Forutsetter Aron-/toelementsmaaling med L2 som felles referanse:
    S = U_12 * conj(I_1) + U_32 * conj(I_3)

Ingen antakelse om symmetriske spenninger eller balansert last.
"""

import cmath
import math


def voltage_phasors(U12, U23, U31, sequence=-1):
    """
    Bygg spenningsfasorene fra de tre maalte linjespenningene.

    KVL gir U_12 + U_23 + U_31 = 0, altsaa en lukket trekant. Tre kjente
    sider bestemmer trekanten entydig (opp til rotasjon og speiling).
    U_12 brukes som vinkelreferanse.

    sequence: -1 for fasesekvens L1-L2-L3, +1 for reversert sekvens.
    """
    for a, b, c in ((U12, U23, U31), (U23, U31, U12), (U31, U12, U23)):
        if a > b + c:
            raise ValueError(
                f"Spenningene {U12}, {U23}, {U31} danner ingen gyldig trekant."
            )

    cos_theta = (U31 ** 2 - U12 ** 2 - U23 ** 2) / (2 * U12 * U23)
    theta = sequence * math.acos(max(-1.0, min(1.0, cos_theta)))

    u12 = complex(U12, 0.0)
    u23 = U23 * cmath.exp(1j * theta)
    return u12, u23, -(u12 + u23)


def solve_missing_current(U12, U23, U31, I1, I3, P, Q, sequence=-1):
    """
    U12, U23, U31 : linjespenninger [V]  (U31 = U13, samme belop)
    I1, I3        : RMS-strom i L1 og L3 [A]
    P             : netto aktiv effekt [W]    (= P_inn - P_ut)
    Q             : netto reaktiv effekt [var] (= Q_inn - Q_ut)

    Returnerer to losninger, mest sannsynlige forst. Hver losning er
    (abs(I2), I1_fasor, I2_fasor, I3_fasor, lead_deg).
    """
    u12, u23, _ = voltage_phasors(U12, U23, U31, sequence)
    u32 = -u23

    S = complex(P, Q)
    rA = abs(u12) * I1          # belop av element 1 sitt bidrag [VA]
    rB = abs(u32) * I3          # belop av element 2 sitt bidrag [VA]

    lo, hi = abs(rA - rB), rA + rB
    if not (lo - 1e-6 <= abs(S) <= hi + 1e-6):
        raise ValueError(
            f"Inkonsistente data: |S| = {abs(S):.1f} VA ligger utenfor "
            f"[{lo:.1f}, {hi:.1f}] VA. Sjekk at alle verdier er lest i samme "
            f"sample, og vurder harmonisk forvrengning."
        )

    cos_psi = (abs(S) ** 2 + rA ** 2 - rB ** 2) / (2 * abs(S) * rA)
    psi = math.acos(max(-1.0, min(1.0, cos_psi)))

    solutions = []
    for sign in (+1, -1):
        A = rA * cmath.exp(1j * (cmath.phase(S) + sign * psi))
        B = S - A
        i1 = (A / u12).conjugate()
        i3 = (B / u32).conjugate()
        i2 = -(i1 + i3)
        lead = math.degrees(cmath.phase(A / B)) if abs(B) > 1e-12 else 0.0
        solutions.append((abs(i2), i1, i2, i3, lead))

    # To speilvendte losninger. Element 1 skal lede element 2
    # (noyaktig 60 grader ved symmetrisk spenning og balansert last).
    solutions.sort(key=lambda r: -r[4])
    return solutions


if __name__ == "__main__":
    # Syntetisk usymmetrisk testtilfelle med kjent fasit.
    u12 = complex(235, 0)
    u23 = 228 * cmath.exp(1j * math.radians(-121))
    u31 = -(u12 + u23)
    i1 = 13.4 * cmath.exp(1j * math.radians(-47))
    i3 = 6.1 * cmath.exp(1j * math.radians(83))
    S = u12 * i1.conjugate() + (-u23) * i3.conjugate()

    print(f"Spenninger : {abs(u12):.1f} / {abs(u23):.1f} / {abs(u31):.1f} V")
    print(f"Fasit      : |I_L2| = {abs(-(i1 + i3)):.4f} A\n")

    for k, r in enumerate(solve_missing_current(
            abs(u12), abs(u23), abs(u31), abs(i1), abs(i3), S.real, S.imag)):
        tag = "Valgt     " if k == 0 else "Forkastet "
        print(f"{tag} : |I_L2| = {r[0]:7.4f} A   (lead = {r[4]:6.2f} grader)")
