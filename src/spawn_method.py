    def spawn(self, goal: str, role: str = "") -> str:
        """
        Spawn a new process with the given goal and role.
        
        Args:
            goal: The goal/task for the process
            role: Optional role identifier for the process
            
        Returns:
            Process ID (pid) of the spawned process
        """
        # Generate unique process ID
        pid = f"proc-{uuid.uuid4().hex[:8]}"
        
        # Create process components
        mmu = self._create_mmu(pid)
        gate = self._create_gate(pid, role)
        decoder = InstructionDecoder()
        
        # Create VCPU
        vcpu = VCPU(
            alu=self._llm,
            config=self.config.vcpu_config,
            decoder=decoder,
            memory=mmu,
            gate=gate,
        )
        
        # Create process
        process = Process(
            pid=pid,
            goal=goal,
            role=role,
            state="PENDING",
            vcpu=vcpu,
            mmu=mmu,
            gate=gate,
        )
        
        # Register process
        self._processes[pid] = process
        
        # Emit spawn event
        self._emit_event("PROC_SPAWNED", pid, {"goal": goal, "role": role})
        
        return pid

