import { useState } from "react";

import { StaffAreaPanel, StaffNavigation, type StaffArea } from "./PortalNavigation";

export type StaffPortalPanels = Readonly<Record<StaffArea, React.ReactNode>>;

export function StaffPortalWorkspace({
  panels,
  initialArea = "overview",
}: {
  panels: StaffPortalPanels;
  initialArea?: StaffArea;
}) {
  const [activeArea, setActiveArea] = useState<StaffArea>(initialArea);

  return (
    <div className="staff-portal-workspace">
      <StaffNavigation active={activeArea} onChange={setActiveArea} />
      <div className="staff-panel-stack">
        {(Object.keys(panels) as StaffArea[]).map((area) => (
          <StaffAreaPanel area={area} active={activeArea === area} key={area}>
            {panels[area]}
          </StaffAreaPanel>
        ))}
      </div>
    </div>
  );
}
